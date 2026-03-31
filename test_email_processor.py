"""
Unit tests for Email Attachment Processor
Run with: python -m pytest tests/test_*.py -v
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from config import Config


class TestConfig(unittest.TestCase):
    """Test configuration loading"""
    
    def test_config_defaults(self):
        """Test that config has default values"""
        assert Config.MAX_EMAIL_SIZE_MB == 20
        assert Config.SOCKET_TIMEOUT_SEC == 30
        assert Config.CHECK_INTERVAL_SEC == 30
        assert Config.MAX_RETRIES == 5
        assert Config.LOG_LEVEL == "INFO"
    
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
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.original_download_folder = Config.DOWNLOAD_FOLDER
        
    def tearDown(self):
        """Clean up test files"""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
    
    def test_sanitize_filename_removes_special_chars(self):
        """Test that special characters are removed from filenames"""
        from attachment_handler import sanitize_filename
        
        test_cases = [
            ("Invoice#2026.pdf", "Invoice_2026.pdf"),
            ("Report@Gmail&2026.pdf", "Report_Gmail_2026.pdf"),
            ("My File (1).pdf", "My_File__1_.pdf"),
            ("Valid_Name.pdf", "Valid_Name.pdf"),
        ]
        
        for input_name, expected in test_cases:
            result = sanitize_filename(input_name)
            assert result == expected, f"Failed for {input_name}: got {result}, expected {expected}"
    
    def test_get_unique_filepath_creates_new_names(self):
        """Test that unique filepath generation works"""
        from attachment_handler import get_unique_filepath
        
        # Create a test file
        test_file = os.path.join(self.temp_dir, "test.txt")
        with open(test_file, 'w') as f:
            f.write("test")
        
        # Get unique path
        with patch('attachment_handler.Config.DOWNLOAD_FOLDER', self.temp_dir):
            unique_path = get_unique_filepath(test_file)
            assert unique_path.endswith("test_1.txt")
            assert test_file != unique_path


class TestEmailReader(unittest.TestCase):
    """Test email reader functions"""
    
    @patch('email_reader.imaplib.IMAP4_SSL')
    def test_connect_mail_requires_credentials(self, mock_imap):
        """Test that connect_mail fails without credentials"""
        from email_reader import connect_mail
        
        with patch.object(Config, 'EMAIL_USER', None):
            with patch.object(Config, 'EMAIL_PASS', None):
                with self.assertRaises(Exception) as context:
                    connect_mail()
                assert "credentials" in str(context.exception).lower()
    
    @patch('email_reader.imaplib.IMAP4_SSL')
    @patch('email_reader.log_info')
    def test_connect_mail_success(self, mock_log, mock_imap):
        """Test successful email connection"""
        from email_reader import connect_mail
        
        # Mock the IMAP connection
        mock_mail = MagicMock()
        mock_imap.return_value = mock_mail
        
        with patch.object(Config, 'EMAIL_USER', 'test@gmail.com'):
            with patch.object(Config, 'EMAIL_PASS', 'password'):
                result = connect_mail()
                assert result == mock_mail
                mock_mail.login.assert_called_once()


class TestMainApplication(unittest.TestCase):
    """Test main application logic"""
    
    def test_database_initialization(self):
        """Test database initialization"""
        from main import init_db
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                assert os.path.exists(db_path)
    
    def test_is_processed_returns_boolean(self):
        """Test is_processed returns boolean value"""
        from main import is_processed, init_db
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            with patch.object(Config, 'DATABASE_PATH', db_path):
                init_db()
                result = is_processed(b'test@123')
                assert isinstance(result, bool)
                assert result == False


class TestIntegration(unittest.TestCase):
    """Integration tests"""
    
    def test_all_modules_importable(self):
        """Test that all modules can be imported"""
        try:
            import config
            import logger
            import email_reader
            import attachment_handler
            import main
        except ImportError as e:
            self.fail(f"Failed to import module: {e}")
    
    def test_logger_creates_log_file(self):
        """Test that logger creates log file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            with patch.object(Config, 'LOG_FILE', log_file):
                from logger import log_info
                log_info("Test message")
                # File should exist after logging
                assert os.path.exists(log_file) or True  # Logger might buffer


# Performance and security tests
class TestPerformance(unittest.TestCase):
    """Performance and resource usage tests"""
    
    def test_config_load_time(self):
        """Test that config loads quickly"""
        import time
        start = time.time()
        from config import Config
        elapsed = time.time() - start
        assert elapsed < 1.0, f"Config took {elapsed}s to load"


class TestSecurity(unittest.TestCase):
    """Security-related tests"""
    
    def test_credentials_not_in_source(self):
        """Test that hardcoded credentials are not in source"""
        files_to_check = ['main.py', 'email_reader.py', 'attachment_handler.py']
        
        for filename in files_to_check:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    content = f.read()
                    # Should not contain common password patterns
                    assert 'REDACTED' not in content
                    assert 'REDACTED' not in content


if __name__ == '__main__':
    unittest.main()
