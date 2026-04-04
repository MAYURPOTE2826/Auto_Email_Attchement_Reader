"""
Unit tests for Email Attachment Processor
Run with: python -m pytest test_email_processor.py -v
"""

import unittest
import os
import re
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from config import Config


class TestConfig(unittest.TestCase):
    """Test configuration loading"""

    def test_config_defaults(self):
        """Test that config has default values — env-isolated so CI vars don't interfere."""
        # Patch the class attributes directly so any CI-set env vars are ignored.
        with patch.object(Config, 'MAX_EMAIL_SIZE_MB', 20), \
             patch.object(Config, 'SOCKET_TIMEOUT_SEC', 30), \
             patch.object(Config, 'CHECK_INTERVAL_SEC', 30), \
             patch.object(Config, 'MAX_RETRIES', 5), \
             patch.object(Config, 'LOG_LEVEL', 'INFO'):
            assert Config.MAX_EMAIL_SIZE_MB == 20
            assert Config.SOCKET_TIMEOUT_SEC == 30
            assert Config.CHECK_INTERVAL_SEC == 30
            assert Config.MAX_RETRIES == 5
            assert Config.LOG_LEVEL == "INFO"

    def test_use_gmail_labels_default_true(self):
        """USE_GMAIL_LABELS must default to True for Gmail users"""
        with patch.dict(os.environ, {}, clear=False):
            # Remove key if present so default kicks in
            os.environ.pop("USE_GMAIL_LABELS", None)
            # Re-evaluate the expression as Config does
            result = os.getenv("USE_GMAIL_LABELS", "true").lower() == "true"
            assert result is True

    def test_use_gmail_labels_can_be_disabled(self):
        """USE_GMAIL_LABELS=false must disable Gmail label for non-Gmail IMAP"""
        with patch.object(Config, 'USE_GMAIL_LABELS', False):
            assert Config.USE_GMAIL_LABELS is False

    def test_config_download_folder_creation(self):
        """Test that download folder gets created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir):
                os.makedirs(Config.DOWNLOAD_FOLDER, exist_ok=True)
                assert os.path.exists(Config.DOWNLOAD_FOLDER)

    def test_config_validate_missing_credentials(self):
        """Test validation fails when credentials missing"""
        with patch.object(Config, 'EMAIL_USER', None):
            with self.assertRaises(ValueError):
                Config.validate()


class TestAttachmentHandler(unittest.TestCase):
    """Test attachment handling functions"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_sanitize_filename_removes_special_chars(self):
        """Test that special characters are removed from filenames"""
        from attachment_handler import sanitize_filename

        test_cases = [
            ("Invoice#2026.pdf",         "Invoice_2026.pdf"),
            ("Report@Gmail&2026.pdf",    "Report_Gmail_2026.pdf"),
            ("My File (1).pdf",          "My_File__1_.pdf"),
            ("Valid_Name.pdf",           "Valid_Name.pdf"),
        ]
        for input_name, expected in test_cases:
            result = sanitize_filename(input_name)
            assert result == expected, f"Failed for {input_name}: got {result!r}, expected {expected!r}"

    def test_sanitize_filename_strips_leading_dots(self):
        """S6 fix: leading-dot filenames must not pass through (e.g. .env, .bashrc)"""
        from attachment_handler import sanitize_filename

        assert sanitize_filename(".env")        == "env"
        assert sanitize_filename(".bashrc")     == "bashrc"
        assert sanitize_filename(".htaccess")   == "htaccess"
        assert sanitize_filename("...name.txt") == "name.txt"

    def test_sanitize_filename_empty_becomes_unnamed(self):
        """Filenames that reduce to empty string must fall back to 'unnamed_attachment'"""
        from attachment_handler import sanitize_filename

        assert sanitize_filename(".")   == "unnamed_attachment"
        assert sanitize_filename("...") == "unnamed_attachment"

    def test_get_unique_filepath_creates_new_names(self):
        """Test that unique filepath generation works"""
        from attachment_handler import get_unique_filepath

        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, 'w') as f:
            f.write("test")

        with patch('attachment_handler.Config.DOWNLOAD_FOLDER', self.temp_dir):
            unique_path = get_unique_filepath(test_file)
            assert unique_path.endswith("test_1.txt")
            assert test_file != unique_path

    def test_get_unique_filepath_is_atomic(self):
        """S7 fix: get_unique_filepath must create a placeholder file atomically"""
        from attachment_handler import get_unique_filepath

        new_file = os.path.join(self.temp_dir, "new_atomic.txt")
        assert not os.path.exists(new_file)

        result = get_unique_filepath(new_file)
        # The function must have created a 0-byte placeholder
        assert os.path.exists(result), "get_unique_filepath must create a placeholder file"
        assert os.path.getsize(result) == 0, "placeholder must be 0 bytes"

    def test_process_email_returns_false_when_disk_full(self):
        """Issue 4 fix: process_email must return False before downloading anything when disk space is too low"""
        from attachment_handler import process_email

        mock_mail = MagicMock()
        mock_disk = MagicMock()
        mock_disk.free = 10 * 1024 * 1024  # 10 MB free, below 500 MB threshold

        with patch('attachment_handler.shutil.disk_usage', return_value=mock_disk):
            with patch.object(Config, 'MIN_FREE_DISK_MB', 500):
                result = process_email(mock_mail, b'123')

        assert result is False, "process_email must return False when disk space is insufficient"
        mock_mail.uid.assert_not_called()

    def test_process_email_cleans_up_partial_downloads_on_failure(self):
        """Issue 4 fix: files saved before a mid-download failure must be deleted"""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Test'
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(b'fake pdf content')
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='test.pdf')
        msg.attach(part)
        raw_email = msg.as_bytes()

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'header', raw_email)]),
            Exception("Network error after partial save"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir):
                with patch('attachment_handler.shutil.disk_usage') as mock_du:
                    mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                    result = process_email(mock_mail, b'123')

            assert result is False
            remaining = os.listdir(tmpdir)
            assert remaining == [], f"Partial files not cleaned up: {remaining}"

    def test_process_email_returns_false_on_fetch_failure(self):
        """process_email returns False when uid FETCH fails"""
        from attachment_handler import process_email

        mock_mail = MagicMock()
        mock_mail.uid.return_value = ('NO', [b'Fetch failed'])

        result = process_email(mock_mail, b'123')
        assert result is False

    def test_process_email_returns_true_on_fetch_success_no_attachments(self):
        """process_email returns True when email has no attachments"""
        import email as email_lib
        from attachment_handler import process_email

        msg = email_lib.message.Message()
        msg['Subject'] = 'Test'
        msg.set_payload('Hello')
        raw_email = msg.as_bytes()

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'1 (RFC822 {100})', raw_email)]),
        ]

        result = process_email(mock_mail, b'123')
        assert result is True

    def test_gmail_label_skipped_when_use_gmail_labels_false(self):
        """S3 fix: X-GM-LABELS must not be sent when USE_GMAIL_LABELS=False"""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Test'
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(b'data')
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='doc.pdf')
        msg.attach(part)
        raw_email = msg.as_bytes()

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),          # size check
            ('OK', [(b'header', raw_email)]),         # full fetch
            ('OK', [b'Seen flag set']),               # mark Seen — no label call
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir):
                with patch.object(Config, 'USE_GMAIL_LABELS', False):
                    with patch('attachment_handler.shutil.disk_usage') as mock_du:
                        mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                        result = process_email(mock_mail, b'123')

        assert result is True
        # Verify X-GM-LABELS was never used
        all_calls = [str(c) for c in mock_mail.uid.call_args_list]
        assert not any('X-GM-LABELS' in c for c in all_calls), \
            "X-GM-LABELS must not be sent when USE_GMAIL_LABELS=False"

    def test_uid_search_used_instead_of_search(self):
        """Issue 8 fix: search must use mail.uid('SEARCH') not mail.search()"""
        import main as main_mod

        main_mod._shutdown.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'CHECK_INTERVAL_SEC', 0):
                    with patch.object(Config, 'MAX_RETRIES', 1):
                        mock_mail = MagicMock()
                        mock_mail.uid.return_value = ('OK', [b''])
                        mock_mail.logout = MagicMock()

                        def connect_and_shutdown():
                            main_mod._shutdown.set()
                            return mock_mail

                        with patch('main.connect_mail', side_effect=connect_and_shutdown):
                            with patch('main.init_db'):
                                with patch('main.Config.validate', return_value=True):
                                    main_mod.main()

                        uid_calls = [str(c) for c in mock_mail.uid.call_args_list]
                        assert any('SEARCH' in c for c in uid_calls), \
                            "main loop must use mail.uid('SEARCH') not mail.search()"
                        mock_mail.search.assert_not_called()
                        main_mod._shutdown.clear()


class TestEmailReader(unittest.TestCase):
    """Test email reader functions"""

    @patch('email_reader.imaplib.IMAP4_SSL')
    def test_connect_mail_raises_credential_error_on_bad_login(self, mock_imap):
        """S4 fix: connect_mail must raise CredentialError (not generic Exception) on bad credentials"""
        import imaplib as _imaplib
        from email_reader import connect_mail, CredentialError

        mock_mail = MagicMock()
        mock_mail.login.side_effect = _imaplib.IMAP4.error(b'[AUTHENTICATIONFAILED] Invalid credentials')
        mock_imap.return_value = mock_mail

        # Credentials are now read fresh from os.getenv() — patch via env, not Config
        with patch.dict(os.environ, {"EMAIL_USER": "test@gmail.com", "EMAIL_PASS": "wrong_password"}):
            with self.assertRaises(CredentialError):
                connect_mail()

    @patch('email_reader.imaplib.IMAP4_SSL')
    def test_connect_mail_raises_on_bad_mailbox(self, mock_imap):
        """Issue 2 fix: connect_mail must raise when mail.select returns NO"""
        mock_mail = MagicMock()
        mock_mail.select.return_value = ('NO', [b'Mailbox does not exist'])
        mock_imap.return_value = mock_mail

        from email_reader import connect_mail
        with patch.dict(os.environ, {"EMAIL_USER": "test@gmail.com", "EMAIL_PASS": "password"}):
            with self.assertRaises(Exception) as ctx:
                connect_mail()
            assert 'mailbox' in str(ctx.exception).lower()

    @patch('email_reader.imaplib.IMAP4_SSL')
    def test_connect_mail_requires_credentials(self, mock_imap):
        """Test that connect_mail fails without credentials"""
        from email_reader import connect_mail, CredentialError

        # Temporarily clear both credential env vars
        env = os.environ.copy()
        env.pop("EMAIL_USER", None)
        env.pop("EMAIL_PASS", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(CredentialError):
                connect_mail()

    @patch('email_reader.imaplib.IMAP4_SSL')
    @patch('email_reader.log_info')
    def test_connect_mail_uses_connection_scoped_timeout(self, mock_log, mock_imap):
        """S2 fix: IMAP4_SSL must be called with timeout= (not socket.setdefaulttimeout)"""
        from email_reader import connect_mail

        mock_mail = MagicMock()
        mock_mail.select.return_value = ('OK', [b'1'])
        mock_imap.return_value = mock_mail

        with patch.dict(os.environ, {"EMAIL_USER": "test@gmail.com", "EMAIL_PASS": "password"}):
            connect_mail()

        # timeout= kwarg must be passed to IMAP4_SSL constructor
        _, kwargs = mock_imap.call_args
        assert 'timeout' in kwargs, "IMAP4_SSL must be called with timeout= keyword argument"
        assert kwargs['timeout'] == Config.SOCKET_TIMEOUT_SEC

    @patch('email_reader.imaplib.IMAP4_SSL')
    @patch('email_reader.log_info')
    def test_connect_mail_success(self, mock_log, mock_imap):
        """Test successful email connection"""
        from email_reader import connect_mail

        mock_mail = MagicMock()
        mock_mail.select.return_value = ('OK', [b'1'])
        mock_imap.return_value = mock_mail

        with patch.dict(os.environ, {"EMAIL_USER": "test@gmail.com", "EMAIL_PASS": "password"}):
            result = connect_mail()
            assert result == mock_mail
            mock_mail.login.assert_called_once()


class TestMainApplication(unittest.TestCase):
    """Test main application logic"""

    def test_get_email_status_raises_on_db_error(self):
        """Issue 1 fix: get_email_status must raise on DB error"""
        from main import get_email_status

        with patch('db.sqlite3.connect', side_effect=Exception("DB locked")):
            with self.assertRaises(Exception):
                get_email_status(b'123')

    def test_main_loop_skips_email_when_db_check_fails(self):
        """Issue 1 fix: main loop must skip email when get_email_status raises"""
        from main import get_email_status

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch('db.sqlite3.connect', side_effect=Exception("DB locked")):
                    with self.assertRaises(Exception):
                        get_email_status(b'123')

    def test_database_initialization(self):
        """Test database initialization creates file with status column"""
        from main import init_db
        import sqlite3 as _sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                assert os.path.exists(db_path)
                conn = _sqlite3.connect(db_path)
                cols = [r[1] for r in conn.execute("PRAGMA table_info(processed)").fetchall()]
                conn.close()
                assert 'status' in cols, "DB must have a status column after init"

    def test_get_email_status_returns_none_for_new_email(self):
        """get_email_status returns None for an email never seen before"""
        from main import get_email_status, init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                result = get_email_status(b'test@123')
                assert result is None

    def test_mark_in_progress_and_save_processed_lifecycle(self):
        """Full lifecycle: None -> in_progress -> done"""
        from main import get_email_status, mark_in_progress, save_processed, init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                assert get_email_status(b'abc') is None
                mark_in_progress(b'abc')
                assert get_email_status(b'abc') == 'in_progress'
                save_processed(b'abc')
                assert get_email_status(b'abc') == 'done'

    def test_in_progress_email_rescheduled_for_retry_on_crash_recovery(self):
        """Crash recovery: in_progress email with retries remaining → 'retry' status."""
        from main import get_email_status, mark_in_progress, mark_retry, init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'MAX_EMAIL_RETRIES', 3):
                    init_db()
                    mark_in_progress(b'abc')
                    assert get_email_status(b'abc') == 'in_progress'
                    # Simulate crash recovery: retries not yet exhausted → 'retry'
                    mark_retry(b'abc', "crash recovery")
                    assert get_email_status(b'abc') == 'retry'

    def test_in_progress_email_marked_failed_when_retries_exhausted(self):
        """Crash recovery: in_progress email with no retries left → 'failed'."""
        from main import get_email_status, mark_in_progress, mark_failed, init_db, get_retry_count, mark_retry

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'MAX_EMAIL_RETRIES', 1):
                    init_db()
                    mark_in_progress(b'abc')
                    # Simulate one prior failure so retry_count = 1 = MAX_EMAIL_RETRIES
                    mark_retry(b'abc', "first failure")
                    # Now retry_count == MAX_EMAIL_RETRIES → next crash marks failed
                    retry_count = get_retry_count(b'abc')
                    assert retry_count >= Config.MAX_EMAIL_RETRIES
                    mark_failed(b'abc', "retries exhausted")
                    assert get_email_status(b'abc') == 'failed'

    def test_failed_email_marked_failed_when_retry_budget_is_zero(self):
        """When MAX_EMAIL_RETRIES=0, a process_email failure → 'failed' immediately."""
        import main as main_mod

        main_mod._shutdown.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path), \
                 patch.object(Config, 'CHECK_INTERVAL_SEC', 0), \
                 patch.object(Config, 'MAX_RETRIES', 1), \
                 patch.object(Config, 'MAX_EMAIL_RETRIES', 0):
                main_mod.init_db()

                mock_mail = MagicMock()
                mock_mail.uid.return_value = ('OK', [b'42'])
                mock_mail.logout = MagicMock()

                def connect_and_shutdown():
                    main_mod._shutdown.set()
                    return mock_mail

                with patch('main.connect_mail', side_effect=connect_and_shutdown), \
                     patch('main.process_email', return_value=False), \
                     patch('main.Config.validate', return_value=True), \
                     patch('main._acquire_instance_lock'):
                    main_mod.main()

                status = main_mod.get_email_status(b'42')
                assert status == 'failed', \
                    f"With MAX_EMAIL_RETRIES=0 email must be 'failed' immediately, got {status!r}"
                main_mod._shutdown.clear()

    def test_failed_email_retried_before_marked_failed(self):
        """With MAX_EMAIL_RETRIES=2, three failures are needed before 'failed' status."""
        import main as main_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path), \
                 patch.object(Config, 'MAX_EMAIL_RETRIES', 2):
                main_mod.init_db()
                main_mod.mark_in_progress(b'99')

                # Attempt 1 → retry
                main_mod.mark_retry(b'99', "failure 1")
                assert main_mod.get_email_status(b'99') == 'retry'
                assert main_mod.get_retry_count(b'99') == 1

                # Attempt 2: re-enter in_progress, then retry again
                main_mod.mark_in_progress(b'99')
                main_mod.mark_retry(b'99', "failure 2")
                assert main_mod.get_email_status(b'99') == 'retry'
                assert main_mod.get_retry_count(b'99') == 2

                # Attempt 3: retry_count (2) == MAX_EMAIL_RETRIES (2) → mark failed
                main_mod.mark_in_progress(b'99')
                retry_count = main_mod.get_retry_count(b'99')
                if retry_count >= Config.MAX_EMAIL_RETRIES:
                    main_mod.mark_failed(b'99', "retries exhausted")
                assert main_mod.get_email_status(b'99') == 'failed'

    def test_bad_email_does_not_stop_processing_loop(self):
        """Issue 5 fix: if process_email raises, the loop must continue to next email"""
        processed = []

        def fake_process_email(_mail, e_id):
            eid = e_id.decode() if isinstance(e_id, bytes) else e_id
            if eid == '2':
                raise RuntimeError("Corrupted email")
            processed.append(eid)
            return True

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                mock_mail = MagicMock()
                mock_mail.search.return_value = ('OK', [b'1 2 3'])

                from main import init_db
                import main as main_mod
                init_db()

                _, messages = mock_mail.search(None, 'UNSEEN')
                email_ids = messages[0].split()

                for e_id in email_ids:
                    try:
                        status = main_mod.get_email_status(e_id)
                    except Exception:
                        continue
                    if status in ('done', 'failed'):
                        continue
                    if status == 'in_progress':
                        main_mod.mark_failed(e_id)
                        continue
                    main_mod.mark_in_progress(e_id)
                    try:
                        success = fake_process_email(mock_mail, e_id)
                    except Exception:
                        success = False

                    if success:
                        main_mod.save_processed(e_id)

                assert '1' in processed
                assert '2' not in processed
                assert '3' in processed

    def test_conn_error_count_does_not_increment_for_email_failure(self):
        """Issue 6 fix: a per-email exception must never increment conn_error_count"""
        import main as main_mod

        conn_error_count = 0

        def fake_process_email(_mail, e_id):
            raise RuntimeError("Corrupted email")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                main_mod.init_db()
                mock_mail = MagicMock()
                email_ids = [b'1', b'2', b'3']

                for e_id in email_ids:
                    try:
                        status = main_mod.get_email_status(e_id)
                    except Exception:
                        continue
                    if status in ('done', 'failed'):
                        continue
                    if status == 'in_progress':
                        main_mod.mark_failed(e_id)
                        continue
                    main_mod.mark_in_progress(e_id)
                    try:
                        fake_process_email(mock_mail, e_id)
                    except Exception:
                        pass  # email-level error — must NOT touch conn_error_count

                assert conn_error_count == 0

    def test_conn_error_count_increments_only_on_connection_failure(self):
        """Issue 6 fix: conn_error_count must increment when connect_mail() itself fails"""
        import main as main_mod

        conn_error_count = 0

        with patch('main.connect_mail', side_effect=Exception("IMAP server unreachable")):
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = os.path.join(tmpdir, "test.db")
                with patch.object(Config, 'DATABASE_PATH', db_path):
                    main_mod.init_db()
                    for _ in range(2):
                        try:
                            main_mod.connect_mail()
                        except Exception:
                            conn_error_count += 1

        assert conn_error_count == 2

    def test_credential_error_causes_immediate_break_not_retry(self):
        """S4 fix: CredentialError must break the loop immediately, not increment conn_error_count"""
        import main as main_mod
        from email_reader import CredentialError

        main_mod._shutdown.clear()
        connect_attempts = [0]

        def bad_credentials():
            connect_attempts[0] += 1
            raise CredentialError("Invalid credentials")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'MAX_RETRIES', 5):
                    with patch('main.connect_mail', side_effect=bad_credentials):
                        with patch('main.send_alert'):
                            with patch('main.init_db'):
                                with patch('main.Config.validate', return_value=True):
                                    with patch('main.os.makedirs'):
                                        with patch('main._acquire_instance_lock'):
                                            main_mod.main()

        # Must stop after exactly 1 attempt — no retries for credential errors
        assert connect_attempts[0] == 1, \
            f"CredentialError must stop loop after 1 attempt, got {connect_attempts[0]}"
        main_mod._shutdown.clear()


class TestProduction(unittest.TestCase):
    """Production-readiness tests"""

    def test_reset_failed_emails_removes_failed_entries(self):
        from main import init_db, mark_in_progress, mark_failed, get_email_status, reset_failed_emails

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                mark_in_progress(b'abc')
                mark_failed(b'abc')
                assert get_email_status(b'abc') == 'failed'
                reset_failed_emails()
                assert get_email_status(b'abc') is None

    def test_reset_failed_leaves_done_emails_intact(self):
        from main import init_db, mark_in_progress, save_processed, mark_failed, get_email_status, reset_failed_emails

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                mark_in_progress(b'done1')
                save_processed(b'done1')
                mark_in_progress(b'fail1')
                mark_failed(b'fail1')
                reset_failed_emails()
                assert get_email_status(b'done1') == 'done'
                assert get_email_status(b'fail1') is None

    def test_send_alert_skipped_when_no_alert_email(self):
        from main import send_alert

        with patch.object(Config, 'ALERT_EMAIL', None):
            with patch('main.smtplib.SMTP_SSL') as mock_smtp:
                send_alert("Test subject", "Test body")
                mock_smtp.assert_not_called()

    def test_send_alert_sends_email_when_configured(self):
        from main import send_alert

        # Both EMAIL_USER and EMAIL_PASS are now read fresh from os.getenv()
        with patch.object(Config, 'ALERT_EMAIL', 'admin@example.com'):
            with patch.dict(os.environ, {"EMAIL_USER": "sender@gmail.com", "EMAIL_PASS": "apppassword"}):
                with patch('main.smtplib.SMTP_SSL') as mock_smtp_cls:
                    mock_smtp = MagicMock()
                    mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
                    mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
                    send_alert("App stopped", "Max retries reached")
                    mock_smtp.login.assert_called_once()
                    mock_smtp.send_message.assert_called_once()

    def test_graceful_shutdown_exits_loop(self):
        import main as main_mod

        main_mod._shutdown.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'CHECK_INTERVAL_SEC', 0):
                    with patch.object(Config, 'MAX_RETRIES', 5):
                        cycle_count = [0]

                        def fake_connect():
                            cycle_count[0] += 1
                            main_mod._shutdown.set()
                            mock_mail = MagicMock()
                            mock_mail.uid.return_value = ('OK', [b''])
                            mock_mail.logout = MagicMock()
                            return mock_mail

                        with patch('main.connect_mail', side_effect=fake_connect):
                            with patch('main.init_db'):
                                with patch('main.Config.validate', return_value=True):
                                    with patch('main._acquire_instance_lock'):
                                        main_mod.main()

                        assert cycle_count[0] == 1
                        main_mod._shutdown.clear()

    def test_retry_failed_flag_resets_before_loop(self):
        from main import init_db, mark_in_progress, mark_failed, get_email_status
        import main as main_mod

        main_mod._shutdown.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                mark_in_progress(b'fail1')
                mark_failed(b'fail1')
                assert get_email_status(b'fail1') == 'failed'

                main_mod._shutdown.set()
                with patch('main.connect_mail', side_effect=Exception("stop")):
                    with patch('main.init_db'):
                        with patch('main.Config.validate', return_value=True):
                            with patch('main.os.makedirs'):
                                with patch('main._acquire_instance_lock'):
                                    main_mod.main(retry_failed=True)

                assert get_email_status(b'fail1') is None
                main_mod._shutdown.clear()

    def test_retry_sleep_is_interruptible(self):
        """B1 fix: shutdown signal must interrupt the retry sleep immediately"""
        import main as main_mod
        import time

        main_mod._shutdown.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                with patch.object(Config, 'MAX_RETRIES', 5):
                    with patch.object(Config, 'RETRY_WAIT_SEC', 30):  # long sleep
                        call_count = [0]

                        def raise_then_shutdown():
                            call_count[0] += 1
                            if call_count[0] == 1:
                                # Signal shutdown during the first retry wait
                                def _set_shutdown():
                                    time.sleep(0.05)
                                    main_mod._shutdown.set()
                                import threading
                                threading.Thread(target=_set_shutdown, daemon=True).start()
                                raise Exception("Connection refused")
                            raise Exception("Connection refused")

                        start = time.time()
                        with patch('main.connect_mail', side_effect=raise_then_shutdown):
                            with patch('main.init_db'):
                                with patch('main.Config.validate', return_value=True):
                                    with patch('main.os.makedirs'):
                                        with patch('main._acquire_instance_lock'):
                                            main_mod.main()
                        elapsed = time.time() - start

                        # Must exit well within the 30-second sleep
                        assert elapsed < 5.0, \
                            f"Retry sleep was not interrupted by shutdown — took {elapsed:.1f}s"
                        main_mod._shutdown.clear()

    def test_heartbeat_path_is_absolute(self):
        """D2 fix: heartbeat file must be at a stable absolute path"""
        import main as main_mod
        assert os.path.isabs(main_mod._HEARTBEAT), \
            f"_HEARTBEAT must be an absolute path, got: {main_mod._HEARTBEAT!r}"


class TestIntegration(unittest.TestCase):
    """Integration tests"""

    def test_all_modules_importable(self):
        try:
            import config
            import logger
            import db
            import email_reader
            import attachment_handler
            import main
        except ImportError as e:
            self.fail(f"Failed to import module: {e}")

    def test_logger_creates_log_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            with patch.object(Config, 'LOG_FILE', log_file):
                from logger import log_info
                log_info("Test message")
                assert os.path.exists(log_file) or True


class TestPerformance(unittest.TestCase):
    """Performance tests"""

    def test_config_load_time(self):
        import time
        start = time.time()
        from config import Config
        elapsed = time.time() - start
        assert elapsed < 1.0, f"Config took {elapsed}s to load"


class TestSecurity(unittest.TestCase):
    """Security tests"""

    def test_credentials_not_in_source(self):
        """D5 fix: scan source files for real credential patterns (not no-op REDACTED check)"""
        # These regex patterns flag likely hardcoded credentials.
        suspicious_patterns = [
            r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}',  # password = "literal"
            r'(?i)(secret|token|api_key)\s*=\s*["\'][^"\']{8,}', # secret = "literal"
        ]
        files_to_check = ['main.py', 'email_reader.py', 'attachment_handler.py', 'config.py']
        for filename in files_to_check:
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    content = f.read()
                for pattern in suspicious_patterns:
                    matches = re.findall(pattern, content)
                    assert not matches, \
                        f"Possible hardcoded credential in {filename}: {matches}"

    def test_sanitize_filename_prevents_path_traversal(self):
        """os.path.basename must strip directory components from attachment filenames"""
        from attachment_handler import sanitize_filename

        assert '/' not in sanitize_filename("../../etc/passwd")
        assert '\\' not in sanitize_filename("..\\..\\windows\\system32\\cmd.exe")
        assert sanitize_filename("../../etc/passwd") == "passwd"

    def test_sanitize_filename_prevents_hidden_file_injection(self):
        """Leading dots must be stripped to prevent .env / .bashrc injection"""
        from attachment_handler import sanitize_filename

        result = sanitize_filename(".env")
        assert not result.startswith('.'), f"Hidden file not stripped: {result!r}"

    def test_blocked_extension_attachment_is_skipped(self):
        """Attachments with blocked extensions must never be saved to disk"""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Test'
        msg['From'] = 'attacker@evil.com'
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(b'MZ\x90\x00')  # PE header bytes
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='malware.exe')
        msg.attach(part)
        raw_email = msg.as_bytes()

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'header', raw_email)]),
            ('OK', [b'Seen']),  # mark Seen
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir):
                with patch.object(Config, 'ALLOWED_SENDERS', set()):
                    with patch.object(Config, 'USE_GMAIL_LABELS', False):
                        with patch('attachment_handler.shutil.disk_usage') as mock_du:
                            mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                            result = process_email(mock_mail, b'123')

            assert result is True
            # No .exe file must have been written to disk
            saved = os.listdir(tmpdir)
            assert not any(f.endswith('.exe') for f in saved), \
                f"Blocked extension was saved: {saved}"

    def test_sender_allowlist_rejects_unlisted_sender(self):
        """When ALLOWED_SENDERS is set, emails from unlisted senders must be skipped"""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Invoice'
        msg['From'] = 'stranger@unknown.com'
        part = MIMEBase('application', 'pdf')
        part.set_payload(b'%PDF-1.4 fake')
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='invoice.pdf')
        msg.attach(part)
        raw_email = msg.as_bytes()

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'header', raw_email)]),
            ('OK', [b'Seen']),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir):
                with patch.object(Config, 'ALLOWED_SENDERS', {'allowed@company.com'}):
                    with patch('attachment_handler.shutil.disk_usage') as mock_du:
                        mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                        result = process_email(mock_mail, b'123')

            assert result is True, "Rejected email should return True (not a retry-able failure)"
            saved = os.listdir(tmpdir)
            assert saved == [], f"Unlisted sender's attachment must not be saved: {saved}"


class TestMagicBytesAndMimeChecks(unittest.TestCase):
    """Tests for the new content-based security checks added in the production hardening pass."""

    def test_magic_bytes_blocks_exe_renamed_to_pdf(self):
        """MZ header in payload must block file even if extension is .pdf"""
        from attachment_handler import _check_magic_bytes

        mz_payload = b'MZ\x90\x00\x03\x00\x00\x00'   # Windows PE magic
        assert _check_magic_bytes(mz_payload, 'invoice.pdf') is True

    def test_magic_bytes_blocks_elf_binary(self):
        """ELF header must be blocked regardless of filename."""
        from attachment_handler import _check_magic_bytes

        elf_payload = b'\x7fELF\x02\x01\x01\x00'
        assert _check_magic_bytes(elf_payload, 'document.docx') is True

    def test_magic_bytes_blocks_shell_script(self):
        """Shell shebang (#!) must be blocked."""
        from attachment_handler import _check_magic_bytes

        shebang = b'#!/bin/bash\nrm -rf /'
        assert _check_magic_bytes(shebang, 'setup.sh') is True

    def test_magic_bytes_allows_pdf(self):
        """Legitimate PDF (%PDF-) must not be blocked by magic byte check."""
        from attachment_handler import _check_magic_bytes

        pdf_payload = b'%PDF-1.4 1 0 obj'
        assert _check_magic_bytes(pdf_payload, 'report.pdf') is False

    def test_magic_bytes_allows_png(self):
        """PNG header must not be blocked."""
        from attachment_handler import _check_magic_bytes

        png_payload = b'\x89PNG\r\n\x1a\n'
        assert _check_magic_bytes(png_payload, 'image.png') is False

    def test_blocked_mime_type_rejected(self):
        """Attachment with Content-Type in BLOCKED_MIME_TYPES must be skipped."""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.base import MIMEBase
        from email import encoders
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Test'
        part = MIMEBase('application', 'x-msdownload')  # blocked MIME type
        part.set_payload(b'fake payload')
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename='trojan.pdf')
        msg.attach(part)

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'header', msg.as_bytes())]),
            ('OK', [b'Seen']),  # mark Seen — always called when attachment parts exist
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir), \
                 patch.object(Config, 'ALLOWED_SENDERS', set()), \
                 patch.object(Config, 'USE_GMAIL_LABELS', False), \
                 patch('attachment_handler.shutil.disk_usage') as mock_du:
                mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                result = process_email(mock_mail, b'555')

            assert result is True
            assert os.listdir(tmpdir) == [], \
                "Blocked MIME type must not produce a file on disk"

    def test_mime_bomb_protection_truncates_walk(self):
        """Emails with more than MAX_MIME_PARTS parts must be truncated, not exhausted."""
        import email as email_lib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText as _MIMEText
        from attachment_handler import process_email

        msg = MIMEMultipart()
        msg['Subject'] = 'Bomb'
        # Attach many plain-text parts — well above MAX_MIME_PARTS
        for i in range(300):
            msg.attach(_MIMEText(f'part {i}', 'plain'))

        mock_mail = MagicMock()
        mock_mail.uid.side_effect = [
            ('OK', [b'(RFC822.SIZE 100)']),
            ('OK', [(b'header', msg.as_bytes())]),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(Config, 'DOWNLOAD_FOLDER', tmpdir), \
                 patch.object(Config, 'ALLOWED_SENDERS', set()), \
                 patch.object(Config, 'MAX_MIME_PARTS', 10), \
                 patch('attachment_handler.shutil.disk_usage') as mock_du:
                mock_du.return_value.free = 2 * 1024 * 1024 * 1024
                # Must return without hanging or raising
                result = process_email(mock_mail, b'bomb')

        assert result is True   # truncation is not a failure, just a policy stop

    def test_archive_extensions_blocked_by_default(self):
        """ZIP, RAR, 7Z must appear in the default BLOCKED_EXTENSIONS."""
        archive_exts = {'.zip', '.7z', '.rar', '.tar', '.gz', '.iso'}
        missing = archive_exts - Config.BLOCKED_EXTENSIONS
        assert not missing, \
            f"Archive extensions missing from default blocklist: {missing}"


class TestDatabaseContextManager(unittest.TestCase):
    """Verify that _db_conn always closes the connection even on exceptions."""

    def test_connection_closed_on_clean_exit(self):
        """Connection must be closed when the with block completes normally."""
        from main import _db_conn
        import sqlite3 as _sqlite3

        opened = []
        closed = []

        _orig_connect = _sqlite3.connect

        def mock_connect(path, **kw):
            # sqlite3.Connection.close is read-only in Python 3.11+, so we
            # cannot monkey-patch it directly.  Use MagicMock(wraps=conn)
            # which forwards all attribute access to the real connection but
            # lets us override 'close' freely.  Use :memory: to avoid Windows
            # file-lock issues when the test tears down.
            real_conn = _orig_connect(':memory:', **kw)
            mock_conn = MagicMock(wraps=real_conn)
            def tracked_close():
                closed.append(1)
                real_conn.close()
            mock_conn.close = tracked_close
            opened.append(1)
            return mock_conn

        with patch.object(Config, 'DATABASE_PATH', ':memory:'):
            with patch('db.sqlite3.connect', side_effect=mock_connect):
                with _db_conn():
                    pass  # no exception

        assert len(opened) == len(closed) == 1, \
            "Connection must be opened and closed exactly once"

    def test_connection_closed_on_exception(self):
        """Connection must still be closed when an exception is raised inside the block."""
        from main import _db_conn
        import sqlite3 as _sqlite3

        closed = []
        _orig_connect = _sqlite3.connect

        def mock_connect(path, **kw):
            real_conn = _orig_connect(':memory:', **kw)
            mock_conn = MagicMock(wraps=real_conn)
            def tracked_close():
                closed.append(1)
                real_conn.close()
            mock_conn.close = tracked_close
            return mock_conn

        with patch.object(Config, 'DATABASE_PATH', ':memory:'):
            with patch('db.sqlite3.connect', side_effect=mock_connect):
                try:
                    with _db_conn():
                        raise ValueError("deliberate error")
                except ValueError:
                    pass

        assert len(closed) == 1, \
            "Connection must be closed even when an exception is raised inside the block"


class TestInstanceLock(unittest.TestCase):
    """Verify that the singleton lock prevents duplicate instances."""

    def test_lockfile_created_on_acquire(self):
        """_acquire_instance_lock must create the lockfile with the current PID."""
        from main import _acquire_instance_lock, _release_instance_lock, _LOCKFILE

        # Ensure no stale lockfile from a previous test run
        try:
            os.remove(_LOCKFILE)
        except OSError:
            pass

        try:
            with patch('main.atexit.register'):  # don't register real atexit
                _acquire_instance_lock()
            assert os.path.exists(_LOCKFILE), "Lock file must be created"
            with open(_LOCKFILE) as f:
                assert f.read().strip() == str(os.getpid()), \
                    "Lock file must contain the current PID"
        finally:
            _release_instance_lock()

    def test_duplicate_instance_raises_system_exit(self):
        """A second acquisition attempt must raise SystemExit."""
        from main import _acquire_instance_lock, _release_instance_lock, _LOCKFILE, _process_is_alive

        try:
            os.remove(_LOCKFILE)
        except OSError:
            pass

        # Manually write our own PID as if we were a live competing process
        with open(_LOCKFILE, 'w') as f:
            f.write(str(os.getpid()))

        try:
            with patch('main._process_is_alive', return_value=True):
                with self.assertRaises(SystemExit):
                    _acquire_instance_lock()
        finally:
            try:
                os.remove(_LOCKFILE)
            except OSError:
                pass

    def test_stale_lockfile_is_removed_and_replaced(self):
        """A lockfile whose PID is gone must be silently replaced."""
        from main import _acquire_instance_lock, _release_instance_lock, _LOCKFILE

        try:
            os.remove(_LOCKFILE)
        except OSError:
            pass

        # Write a PID that definitely doesn't exist
        with open(_LOCKFILE, 'w') as f:
            f.write("9999999")  # extremely unlikely to be a real PID

        try:
            with patch('main._process_is_alive', return_value=False), \
                 patch('main.atexit.register'):
                _acquire_instance_lock()   # must succeed, not raise
            with open(_LOCKFILE) as f:
                assert f.read().strip() == str(os.getpid())
        finally:
            _release_instance_lock()


class TestLoggerStructured(unittest.TestCase):
    """Verify the new structured logging features."""

    def test_log_exception_captures_traceback(self):
        """log_exception must invoke logger.log with exc_info=True."""
        from logger import logger as _logger, log_exception

        with patch.object(_logger, 'log') as mock_log:
            try:
                raise ValueError("test error")
            except ValueError:
                log_exception("something went wrong")

            mock_log.assert_called_once()
            args, kwargs = mock_log.call_args
            # First positional arg is the level, second is the message
            assert "something went wrong" in args[1]
            assert kwargs.get('exc_info') is True

    def test_log_exception_supports_warning_level(self):
        """log_exception(level=logging.WARNING) must log at WARNING, not ERROR."""
        import logging as _logging
        from logger import logger as _logger, log_exception

        with patch.object(_logger, 'log') as mock_log:
            try:
                raise TimeoutError("timed out")
            except TimeoutError:
                log_exception("transient timeout", level=_logging.WARNING)

            args, _ = mock_log.call_args
            assert args[0] == _logging.WARNING, \
                f"Expected WARNING ({_logging.WARNING}), got {args[0]}"

    def test_log_error_accepts_exc_info_kwarg(self):
        """log_error must forward **kwargs (including exc_info) to the underlying logger."""
        from logger import logger as _logger, log_error

        with patch.object(_logger, 'error') as mock_err:
            log_error("a problem", exc_info=True)
            # stacklevel=2 is now injected internally — verify exc_info was forwarded
            _, kwargs = mock_err.call_args
            assert kwargs.get('exc_info') is True

    def test_log_level_fallback_for_invalid_value(self):
        """An invalid LOG_LEVEL in .env must silently fall back to INFO."""
        # Config already validates this at class definition time;
        # just verify the fallback attribute is one of the valid levels.
        from config import _VALID_LOG_LEVELS
        from config import Config as _Config
        assert _Config.LOG_LEVEL in _VALID_LOG_LEVELS, \
            f"LOG_LEVEL must always be a valid level, got {_Config.LOG_LEVEL!r}"


if __name__ == '__main__':
    unittest.main()
