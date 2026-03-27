import time
import os
from email_reader import connect_mail
from attachment_handler import process_email
from logger import log_info, log_error


def load_processed():
    if not os.path.exists("processed_emails.txt"):
        return set()

    with open("processed_emails.txt", "r") as f:
        return set(line.strip() for line in f)


def save_processed(e_id):
    with open("processed_emails.txt", "a") as f:
        f.write(e_id.decode() + "\n")


def main():
    retry_count = 0

    while retry_count < 5:
        try:
            print("\n Checking for new emails...")

            mail = connect_mail()

            status, messages = mail.search(None, 'UNSEEN')
            email_ids = messages[0].split()

            print(f" Found {len(email_ids)} new emails")

            processed = load_processed()

            for e_id in email_ids:
                print(f"Processing Email ID: {e_id.decode()}")

                if e_id.decode() in processed:
                    print(" Already processed, skipping...")
                    continue

                success = process_email(mail, e_id)

                if success:
                    save_processed(e_id)
                    print("✅ Processed successfully")

            mail.logout()

            retry_count = 0

            print("⏳ Waiting for 30 seconds...\n")
            time.sleep(30)

        except Exception as e:
            retry_count += 1
            log_error(f"Main Error: {str(e)}")

            print(f"❌ Error occurred: {str(e)}")

            if retry_count >= 5:
                log_error("Max retries reached. Stopping script.")
                print("🛑 Max retries reached. Exiting...")
                break

            time.sleep(10)


if __name__ == "__main__":
    main()