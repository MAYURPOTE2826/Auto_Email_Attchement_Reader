import os
import email
from logger import log_info, log_error

DOWNLOAD_FOLDER = "attachments"

if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)


def sanitize_filename(filename):
    return os.path.basename(filename)


def get_unique_filepath(filepath):
    base, ext = os.path.splitext(filepath)
    counter = 1

    while os.path.exists(filepath):
        filepath = f"{base}_{counter}{ext}"
        counter += 1

    return filepath


def process_email(mail, e_id):
    try:
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

                            filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                            filepath = get_unique_filepath(filepath)

                            payload = part.get_payload(decode=True)

                            if payload:
                                with open(filepath, "wb") as f:
                                    f.write(payload)

                                log_info(f"Downloaded: {filepath}")

        if has_attachment:
            mail.store(e_id, '+X-GM-LABELS', 'Attachment_Mails')
            mail.store(e_id, '+FLAGS', '\\Seen')
            log_info("Label assigned")

        return True

    except Exception as e:
        log_error(f"Processing Error: {str(e)}")
        return False