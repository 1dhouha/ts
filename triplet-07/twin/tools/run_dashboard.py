"""Convergence City local dashboard launcher. EOF, quit and Ctrl+C stop HTTP and the city owner."""
import argparse
import getpass
import os
from pathlib import Path
import sys
import uuid
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from city_dashboard.security import Sessions
from city_dashboard.server import DashboardBridge
from city_live.runtime import LiveCity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='Open the loopback dashboard in your default browser')
    args = parser.parse_args()
    city, bridge = None, None
    try:
        if not 0 <= args.port <= 65535:
            raise ValueError('Port must be 0-65535')
        if sys.stdin.isatty():
            secret = getpass.getpass('Choose a one-launch operator passphrase (16-256 characters): ')
        else:
            # Automation can pipe one secret line; never echo it or accept it in argv/URL.
            secret = sys.stdin.readline(258).rstrip('\r\n')
        Sessions(secret)
        persistence_path = Path(os.environ.get(
            'TWIN_STATE_DB_PATH', str(Path(__file__).resolve().parents[1]
                                      / 'logs' / 'twin-state.sqlite3'),
        ))
        city = LiveCity(
            'dashboard-' + uuid.uuid4().hex[:16],
            persistence_path=persistence_path,
        )
        bridge = DashboardBridge(city, secret, port=args.port)
        del secret
        bridge.start()
        city.start()
        print(f'Convergence City dashboard: {bridge.url}', flush=True)
        print('Open this URL. Use the same launch passphrase for private operator access.', flush=True)
        print('Reported view needs no passphrase. Type quit, EOF or Ctrl+C to stop.', flush=True)
        if args.open:
            try:
                webbrowser.open(bridge.url)
            except (webbrowser.Error, OSError):
                print('Open the printed local URL manually.', flush=True)
        for line in sys.stdin:
            if line.strip().lower() in ('quit', 'exit'):
                break
        return 0
    except (KeyboardInterrupt, EOFError):
        return 0
    except OSError:
        print('PORT_UNAVAILABLE: choose a different local port.', file=sys.stderr)
        return 2
    except ValueError:
        print('INVALID_ARGUMENT: port 0-65535 and passphrase length 16-256 required.', file=sys.stderr)
        return 2
    finally:
        if bridge is not None:
            bridge.close()
        elif city is not None:
            city.close()
        if city is not None:
            print('Owner stopped.', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
