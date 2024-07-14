import os
import subprocess
import logging
import time
import paramiko
import schedule
from tqdm import tqdm
from dotenv import load_dotenv, dotenv_values
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from plyer import notification
from threading import Lock, Thread
import tkinter as tk
from tkinter import messagebox
import queue

# Global locks for thread-safe file operations
file_lock = Lock()
active_transfer_lock = Lock()
active_transfer = False
lecteur = None
usb_prompted = False  # Flag to indicate if the user has been prompted for USB backup
usb_queue = queue.Queue()


def load_all_configurations():
    """Load and validate configurations for all servers from environment variables."""
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)
    all_configs = []
    env_vars = dotenv_values(dotenv_path)

    logging.info(f"Loaded environment variables from {dotenv_path}")

    # Detect server indexes
    server_indexes = set()
    for key in env_vars:
        if key.startswith("SERVER_") and key.endswith("_IP"):
            index = key.split('_')[1]
            server_indexes.add(index)

    logging.info(f"Detected server indexes: {server_indexes}")

    for index in server_indexes:
        config = {
            'server': env_vars.get(f"SERVER_{index}_IP", ""),
            'username': env_vars.get(f"SERVER_{index}_USERNAME", ""),
            'password': env_vars.get(f"SERVER_{index}_PASSWORD", ""),
            'source_path': env_vars.get(f"SERVER_{index}_SOURCE_PATH", ""),
            'primary_backup_path': env_vars.get(f"SERVER_{index}_PRIMARY_BACKUP_PATH", "").replace('/', '\\'),
            'secondary_backup_paths': [path.replace('/', '\\') for path in
                                       env_vars.get(f"SERVER_{index}_SECONDARY_BACKUP_PATHS", "").split(',') if path],
            'sendgrid_api_key': env_vars.get("SENDGRID_API_KEY", ""),
            'email_sender': env_vars.get("EMAIL_SENDER", ""),
            'email_recipient': env_vars.get("EMAIL_RECIPIENT", ""),
            'send_mail': env_vars.get("SEND_MAIL", "no").lower(),
            'backup_times': env_vars.get("BACKUP_TIMES", "03:00,15:00").split(',')
        }

        required_keys = ['server', 'username', 'password', 'source_path', 'primary_backup_path']
        if all(config[key] for key in required_keys):
            all_configs.append(config)
            logging.info(f"Loaded configuration for server index: {index}")
        else:
            logging.warning(f"Configuration for server index {index} is missing or invalid.")

    logging.info(f"Total servers loaded: {len(all_configs)}")
    return all_configs


def setup_logging():
    """Set up the logging configuration."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='backup.log')
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)
    logging.getLogger().addHandler(console)


def send_email(subject, body, config):
    """Send email notifications using SendGrid."""
    if config['send_mail'] == 'yes':
        message = Mail(
            from_email=config['email_sender'],
            to_emails=config['email_recipient'],
            subject=subject,
            plain_text_content=body
        )
        try:
            sg = SendGridAPIClient(config['sendgrid_api_key'])
            response = sg.send(message)
            logging.info(f"Email sent to {config['email_recipient']} with status code {response.status_code}")
        except Exception as e:
            logging.error(f"Failed to send email: {e}")


def show_notification(title, message, timeout=10):
    """Show Windows toast notifications using plyer."""
    try:
        notification.notify(title=title, message=message, timeout=timeout)
    except Exception as e:
        logging.error(f"Failed to show notification: {e}")


def create_backup_directories(config):
    """Create backup directories if they do not exist."""
    for path in [config['primary_backup_path']] + config['secondary_backup_paths']:
        os.makedirs(path, exist_ok=True)
        logging.info(f"Ensured backup directory exists: {path}")


def connect_ssh(config):
    """Establish SSH connection."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    retries = 3
    for attempt in range(retries):
        try:
            ssh.connect(config['server'], username=config['username'], password=config['password'])
            logging.info(f"SSH connection established for server {config['server']}")
            return ssh
        except paramiko.AuthenticationException:
            logging.error(f"Authentication failed for server {config['server']}.")
            send_email("Backup Script Error", f"Authentication failed for server {config['server']}.", config)
            show_notification("Backup Script Error", f"Authentication failed for server {config['server']}.")
            return None
        except Exception as e:
            logging.error(f"Failed to connect to SSH server {config['server']}: {e}")
            if attempt < retries - 1:
                logging.info(f"Retrying... ({attempt + 1}/{retries})")
                time.sleep(5)
            else:
                send_email("Backup Script Error", f"Failed to connect to SSH server {config['server']}: {e}", config)
                show_notification("Backup Script Error", f"Failed to connect to SSH server {config['server']}: {e}")
                return None


def sanitize_filename(filename):
    """Sanitize the filename for use in the Windows file system."""
    return "".join(c if c.isalnum() or c in (' ', '.', '_', '-') else '_' for c in filename)


def perform_backup(config):
    """Perform the backup operations."""
    logging.info(f"Starting backup for server {config['server']}")

    ssh = connect_ssh(config)
    if not ssh:
        return False

    try:
        sftp = ssh.open_sftp()

        create_backup_directories(config)
        show_notification("Backup Script", f"Starting backup process for server {config['server']}.")

        logging.info(f"Retrieving list of backup files from {config['source_path']}")
        stdin, stdout, stderr = ssh.exec_command(f"ls -lt --time-style=+%s {config['source_path']}")
        remote_files = stdout.read().decode().splitlines()

        with file_lock:
            local_files = set(os.listdir(config['primary_backup_path']))

        new_files = []
        for line in remote_files:
            parts = line.split()
            if len(parts) >= 6:
                filename = parts[-1]
                sanitized_filename = sanitize_filename(filename)
                if sanitized_filename not in local_files:
                    new_files.append((filename, int(parts[-2])))

        logging.info(f"New files to download: {len(new_files)}")

        if not new_files:
            logging.info("No new files to download.")
            show_notification("Backup Script", f"No new files to download for server {config['server']}.")
            return

        temp_dir = os.path.join(config['primary_backup_path'], "temp")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            with active_transfer_lock:
                global active_transfer
                active_transfer = True

            for i, (file, mtime) in enumerate(new_files):
                sanitized_file = sanitize_filename(file)
                try:
                    remote_file_path = os.path.join(config['source_path'], file).replace('\\', '/')
                    temp_file_path = os.path.join(temp_dir, sanitized_file)

                    if os.path.exists(temp_file_path) or os.path.exists(
                            os.path.join(config['primary_backup_path'], sanitized_file)):
                        logging.info(f"File {sanitized_file} already exists, skipping download.")
                        continue

                    file_size = sftp.stat(remote_file_path).st_size

                    with open(temp_file_path, 'wb') as f, tqdm(total=file_size, unit="B", unit_scale=True,
                                                               unit_divisor=1024, miniters=1,
                                                               desc=f"Downloading {sanitized_file}") as pbar:
                        sftp.getfo(remote_file_path, f, callback=lambda x, y: pbar.update(x - pbar.n))

                        # Add throttling
                        time.sleep(0.1)  # Adjust the sleep time as necessary

                    logging.info(f"Downloaded {sanitized_file}")

                    final_file_path = os.path.join(config['primary_backup_path'], sanitized_file)
                    os.rename(temp_file_path, final_file_path)
                    logging.info(f"Moved {sanitized_file} to {final_file_path}")

                    # Set the modification time to match the source file
                    os.utime(final_file_path, (mtime, mtime))

                except Exception as e:
                    logging.error(f"Failed to copy {sanitized_file}: {e}")
                    send_email("Backup Script Error", f"Failed to copy {sanitized_file}: {e}", config)
                    show_notification("Backup Script Error", f"Failed to copy {sanitized_file}: {e}")

            cleanup_temp_directory(temp_dir)

            synchronize_directories(config['primary_backup_path'], config['secondary_backup_paths'])

            logging.info(f"Backup complete for server {config['server']}.")
            send_email("Backup Script Success", f"Backup complete for server {config['server']}.", config)
            show_notification("Backup Script Success", f"Backup complete for server {config['server']}.")
            return True

        except KeyboardInterrupt:
            logging.info("Backup process interrupted by user. Exiting...")
            show_notification("Backup Script Interrupted", "Backup process interrupted by user. Exiting...", timeout=5)
            return False
        except Exception as e:
            logging.error(f"Unexpected error occurred during backup: {e}")
            send_email("Backup Script Error", f"Unexpected error occurred during backup: {e}", config)
            show_notification("Backup Script Error", f"Unexpected error occurred during backup: {e}")
            return False
        finally:
            with active_transfer_lock:
                active_transfer = False
            if sftp:
                sftp.close()
            logging.info("SFTP connection closed.")
    finally:
        if ssh:
            ssh.close()
        logging.info("SSH connection closed.")


def cleanup_temp_directory(temp_dir):
    """Clean up the temporary directory."""
    try:
        for filename in os.listdir(temp_dir):
            file_path = os.path.join(temp_dir, filename)
            os.remove(file_path)
        os.rmdir(temp_dir)
    except Exception as e:
        logging.error(f"Failed to clean up temporary directory: {e}")


def synchronize_directories(primary_backup_path, secondary_backup_paths):
    """Synchronize files from the primary backup directory to secondary directories."""
    primary_files = set(os.listdir(primary_backup_path))
    for secondary_path in secondary_backup_paths:
        os.makedirs(secondary_path, exist_ok=True)
        secondary_files = set(os.listdir(secondary_path))
        files_to_copy = primary_files - secondary_files
        for file in files_to_copy:
            src_file = os.path.join(primary_backup_path, file)
            dest_file = os.path.join(secondary_path, file)
            try:
                file_size = os.path.getsize(src_file)
                with open(src_file, 'rb') as src, open(dest_file, 'wb') as dst, tqdm(total=file_size, unit="B",
                                                                                     unit_scale=True, unit_divisor=1024,
                                                                                     miniters=1,
                                                                                     desc=f"Copying {file} to {secondary_path}") as pbar:
                    while True:
                        buf = src.read(1024 * 1024)  # Read in 1MB chunks
                        if not buf:
                            break
                        dst.write(buf)
                        pbar.update(len(buf))
                logging.info(f"Copied {file} to {secondary_path}")
            except Exception as e:
                logging.error(f"Failed to copy {file} to {secondary_path}: {e}")
                send_email("Backup Script Error", f"Failed to copy {file} to {secondary_path}: {e}")
                show_notification("Backup Script Error", f"Failed to copy {file} to {secondary_path}: {e}")


def prompt_user_for_backup(usb_path):
    """Prompt the user for USB backup using a Tkinter popup."""
    root = tk.Tk()
    root.withdraw()  # Hide the root window
    response = messagebox.askyesno("USB Backup",
                                   f"USB drive detected at {usb_path}. Do you want to transfer the backup to this drive?")
    root.destroy()
    return response


def select_backup_folder(usb_path):
    """Select or create a backup folder on the USB drive."""
    folder_selected = input(
        f"Enter the backup folder path on the USB drive {usb_path} (leave blank to create a new folder): ")
    if not folder_selected:
        folder_name = input("Enter new folder name: ")
        if folder_name:
            folder_selected = os.path.join(usb_path, folder_name)
            os.makedirs(folder_selected, exist_ok=True)
    return folder_selected


def copy_to_usb(usb_path, backup_path):
    target_folder = select_backup_folder(usb_path)
    if not target_folder:
        logging.info("No folder selected or created for backup. Skipping backup to USB.")
        return

    wait_for_no_active_transfer()
    with active_transfer_lock:
        global active_transfer
        active_transfer = True
    try:
        for file_name in os.listdir(backup_path):
            src_file = os.path.join(backup_path, file_name)
            dest_file = os.path.join(target_folder, file_name)
            if os.path.exists(dest_file):
                logging.info(f"File {file_name} already exists at {target_folder}, skipping copy.")
                continue
            try:
                file_size = os.path.getsize(src_file)
                with open(src_file, 'rb') as src, open(dest_file, 'wb') as dst, tqdm(total=file_size, unit="B",
                                                                                     unit_scale=True, unit_divisor=1024,
                                                                                     miniters=1,
                                                                                     desc=f"Copying {file_name} to {target_folder}") as pbar:
                    while True:
                        buf = src.read(1024 * 1024)  # Read in 1MB chunks
                        if not buf:
                            break
                        dst.write(buf)
                        pbar.update(len(buf))
                logging.info(f"Copied {file_name} to {target_folder}")
            except Exception as e:
                logging.error(f"Failed to copy {file_name} to {target_folder}: {e}")
    finally:
        with active_transfer_lock:
            active_transfer = False


def check_usb(config):
    global lecteur
    previous_usb = None
    while True:
        output = subprocess.check_output("wmic logicaldisk get caption, drivetype", shell=True)
        data = str(output)
        x = data.find("2")
        if x != -1:
            get = data.find("2")
            cvt = int(get)
            divise = cvt - 9
            getD = data[divise:cvt]
            current_usb = getD[0:2]
            if current_usb != previous_usb:
                previous_usb = current_usb
                lecteur = current_usb
                logging.info(f"Your USB label: {lecteur}")
                if current_usb not in usb_prompted:
                    usb_queue.put((lecteur, config))
                    usb_prompted.add(current_usb)
        else:
            previous_usb = None
        time.sleep(10)


def process_usb_queue():
    try:
        while not usb_queue.empty():
            usb_path, config = usb_queue.get_nowait()
            if prompt_user_for_backup(usb_path):
                copy_to_usb(usb_path, config['primary_backup_path'])
    except queue.Empty:
        pass


def wait_for_no_active_transfer():
    while True:
        with active_transfer_lock:
            if not active_transfer:
                break
        time.sleep(1)


def safe_eject_usb():
    global lecteur
    if lecteur:
        with active_transfer_lock:
            if active_transfer:
                logging.warning("Cannot eject USB drive. Active transfer in progress.")
                show_notification("USB Eject Warning", "Cannot eject USB drive. Active transfer in progress.")
            else:
                response = input(f"Do you want to safely eject the USB drive {lecteur}? (yes/no): ")
                if response.strip().lower() == 'yes':
                    try:
                        subprocess.check_call(f"powershell (Get-Volume -DriveLetter {lecteur[0]}).DriveLetter")
                        logging.info(f"USB drive {lecteur} ejected safely.")
                        lecteur = None
                        usb_prompted.clear()  # Reset prompted set
                    except Exception as e:
                        logging.error(f"Failed to eject USB drive {lecteur}: {e}")
                        show_notification("USB Eject Error", f"Failed to eject USB drive {lecteur}: {e}")


def schedule_backups(config, backup_times):
    """Schedule backups for a specific server configuration."""
    for backup_time in backup_times:
        try:
            schedule.every().day.at(backup_time).do(perform_backup, config)
            logging.info(f"Scheduled backup for server {config['server']} at {backup_time}")
        except schedule.ScheduleValueError as e:
            logging.error(f"Invalid backup time format '{backup_time}' for server {config['server']}: {e}")


def main():
    setup_logging()
    configs = load_all_configurations()

    for config in configs:
        perform_backup(config)

    usb_threads = []
    for config in configs:
        usb_thread = Thread(target=check_usb, args=(config,))
        usb_thread.start()
        usb_threads.append(usb_thread)

        schedule_backups(config, config['backup_times'])

    try:
        while True:
            schedule.run_pending()
            process_usb_queue()  # Check the queue for USB messages
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Backup process interrupted by user. Exiting...")
        show_notification("Backup Script Interrupted", "Backup process interrupted by user. Exiting...", timeout=5)
    finally:
        for usb_thread in usb_threads:
            usb_thread.join()


if __name__ == "__main__":
    main()
