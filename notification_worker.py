import argparse
import os
import time
from app import app, watering
from notifications import dispatch_due

def main():
    parser = argparse.ArgumentParser(description='Procesa riegos pendientes por correo')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()
    if args.interval < 15:
        parser.error('El intervalo mínimo es 15 segundos')
    while True:
        result = dispatch_due(app.state.db, watering,
                              grace_minutes=int(os.getenv('NOTIFICATION_GRACE_MINUTES', '30')),
                              timezone_name=os.getenv('NOTIFICATION_TIMEZONE', 'America/Santiago'))
        print(result, flush=True)
        if args.once:
            return
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
