import os
import email
from config import Config
from logger import log_info, log_error
import re

# Create download folder if it doesn't exist
if not os.path.exists(Config.DOWNLOAD_FOLDER):
    os.makedirs(Config.DOWNLOAD_FOLDER)



def sanitize_filename(filename):
    filename = os.path.basename(filename)
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    return filename


def get_unique_filepath(filepath):
    """Generate unique filepath if file already exists"""
    base, ext = os.path.splitext(filepath)
    counter = 1

    while os.path.exists(filepath):
        if counter > Config.MAX_FILENAME_ATTEMPTS:
            raise RuntimeError(f"Could not generate unique filepath after {Config.MAX_FILENAME_ATTEMPTS} attempts")
        filepath = f"{base}_{counter}{ext}"
        counter += 1

    return filepath


def process_email(mail, e_id):
    """Process email and download attachments with size limit"""
    try:
        size_status, size_data = mail.fetch(e_id, "(RFC822.SIZE)")
        if size_status == 'OK' and size_data[0]:
            size_str = size_data[0].decode()
            match = re.search(r'RFC822\.SIZE (\d+)', size_str)
            if match:
                size_in_bytes = int(match.group(1))
                max_size_bytes = Config.MAX_EMAIL_SIZE_MB * 1024 * 1024
                if size_in_bytes > max_size_bytes:
                    log_error(f"Email {e_id} is too large ({size_in_bytes} bytes). Skipping.")
                    mail.store(e_id, '+FLAGS', '\\Seen')
                    return True

        status, msg_data = mail.fetch(e_id, "(RFC822)")
        has_attachment = False

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])

                subject = msg.get("Subject")
                log_info(f"Processing Email: {subject}")

                for part in msg.walk():
                    if part.get_content_disposition() == 'attachment':
                        has_attachment = True

                        filename = part.get_filename()
                        if filename:
                            filename = sanitize_filename(filename)

                            filepath = os.path.join(Config.DOWNLOAD_FOLDER, filename)
                            filepath = get_unique_filepath(filepath)

                            payload = part.get_payload(decode=True)

                            if payload:
                                with open(filepath, "wb") as f:
                                    f.write(payload)

                                log_info(f"Downloaded: {filepath}")

        if has_attachment:
            label_status, label_resp = mail.store(e_id, '+X-GM-LABELS', 'Attachment_Mails')
            if label_status != 'OK':
                log_error(f"Failed to apply label: {label_resp}")
                return False

            seen_status, seen_resp = mail.store(e_id, '+FLAGS', '\\Seen')
            if seen_status != 'OK':
                log_error(f"Failed to mark as seen: {seen_resp}")
                return False

            log_info("Label assigned")

        return True

    except Exception as e:
        log_error(f"Processing Error: {str(e)}")
        return False