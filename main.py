import time
import sqlite3
from config import Config
from email_reader import connect_mail
from attachment_handler import process_email
from logger import log_info, log_error


def init_db():
    """Initialize database and create table if it doesn't exist"""
    try:
        conn = sqlite3.connect(Config.DATABASE_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS processed (email_id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        log_info(f"Database initialized at {Config.DATABASE_PATH}")
    except Exception as e:
        log_error(f"Database initialization error: {str(e)}")
        raise

def is_processed(e_id):
    """Check if email has already been processed"""
    try:
        conn = sqlite3.connect(Config.DATABASE_PATH)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM processed WHERE email_id = ?", (e_id.decode(),))
        res = cur.fetchone() is not None
        conn.close()
        return res
    except Exception as e:
        log_error(f"Database check error: {str(e)}")
        return False

def save_processed(e_id):
    """Mark email as processed in database"""
    try:
        conn = sqlite3.connect(Config.DATABASE_PATH)
        conn.execute("INSERT OR IGNORE INTO processed (email_id) VALUES (?)", (e_id.decode(),))
        conn.commit()
        conn.close()
    except Exception as e:
        log_error(f"Database save error: {str(e)}")


def main():
    """Main application loop"""
    try:
        Config.validate()
    except ValueError as e:
        log_error(f"Configuration error: {str(e)}")
        print(f"❌ Configuration error: {str(e)}")
        return
    
    init_db()
    retry_count = 0

    while retry_count < Config.MAX_RETRIES:
        mail = None   

        try:
            print("\n🔄 Checking for new emails...")
            log_info("Starting email check cycle")

            mail = connect_mail()

            status, messages = mail.search(None, 'UNSEEN')
            email_ids = messages[0].split()

            print(f"📩 Found {len(email_ids)} new emails")
            log_info(f"Found {len(email_ids)} unseen emails")

            if len(email_ids) > 0:
                for e_id in email_ids:
                    print(f"➡️ Processing Email ID: {e_id.decode()}")

                    if is_processed(e_id):
                        print("⏩ Already processed, skipping...")
                        continue

                    success = process_email(mail, e_id)

                    if success:
                        save_processed(e_id)
                        print("✅ Processed successfully")
                    else:
                        print("⚠️ Processing failed, will retry")
            else:
                print("No new emails found")
                log_info("No unseen emails found")

            retry_count = 0

            print(f"⏳ Waiting {Config.CHECK_INTERVAL_SEC} seconds...\n")
            log_info(f"Waiting {Config.CHECK_INTERVAL_SEC} seconds before next check")
            time.sleep(Config.CHECK_INTERVAL_SEC)

        except Exception as e:
            error_msg = str(e)
            log_error(f"Main Error: {error_msg}")
            print(f"❌ Error occurred: {error_msg}")

            if "credentials" in error_msg.lower():
                print("🛑 Fatal error: Check your email/password in .env")
                log_error("Fatal error: Invalid credentials")
                break

            retry_count += 1

            if retry_count >= Config.MAX_RETRIES:
                log_error(f"Max retries ({Config.MAX_RETRIES}) reached. Stopping script.")
                print(f"🛑 Max retries ({Config.MAX_RETRIES}) reached. Exiting...")
                break

            print(f"🔁 Retrying... Attempt {retry_count}/{Config.MAX_RETRIES}")
            log_info(f"Retry attempt {retry_count}/{Config.MAX_RETRIES}, waiting {Config.RETRY_WAIT_SEC} seconds")
            time.sleep(Config.RETRY_WAIT_SEC)

        finally:
            if mail:
                try:
                    mail.logout()
                    print("🔌 Connection closed")
                    log_info("Connection closed successfully")
                except Exception as e:
                    log_error(f"Logout Error: {str(e)}")
                    print(f"⚠️ Logout error: {str(e)}")


if __name__ == "__main__":
    main()