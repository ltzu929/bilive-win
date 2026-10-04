"""Modern workbench entry; does not initialize upload DB or start Worker on read."""
import argparse
import os
from pathlib import Path
from bilive.config.secrets import load_secret_env
from bilive.network import use_direct_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=2234)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_secret_env(root / '.secrets/env')
    use_direct_network()
    import uvicorn
    uvicorn.run('bilive.api.app:app', host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
