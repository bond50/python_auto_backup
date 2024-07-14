import os
from dotenv import load_dotenv


def extract_backup_times():
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)
    backup_times = os.getenv('BACKUP_TIMES', "04:00,15:00").split(',')
    return backup_times


if __name__ == "__main__":
    times = extract_backup_times()
    print(",".join(times))
