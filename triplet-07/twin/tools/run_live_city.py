"""Launch from any directory using installed Python; no installation required."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from city_live import LiveCity, LiveError
from city_live.cli import console


def main():
    parser = argparse.ArgumentParser(description='Local privileged Twin operator console')
    parser.add_argument('--run-id', default='live-001')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--epoch', default='2026-10-18T00:00:00Z')
    parser.add_argument('--speed', type=float, default=1)
    args = parser.parse_args()
    try:
        city = LiveCity(args.run_id, seed=args.seed, epoch=args.epoch)
        try:
            city.set_speed(args.speed)
            console(city, sys.stdin, sys.stdout, interactive=sys.stdin.isatty())
        finally:
            city.close()
    except KeyboardInterrupt:
        return 0
    except LiveError as error:
        print(error.code, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
