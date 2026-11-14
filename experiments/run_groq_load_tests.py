"""Explicitly budgeted live integration test. Defaults to dry-run (zero calls)."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from reliability.load_testing import LoadConfig, LoadRunner, SCENARIOS, summarize, write_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider',choices=('groq','standin'),default='groq')
    parser.add_argument('--scenario',choices=SCENARIOS,default='baseline')
    parser.add_argument('--max-requests',type=int,required=True)
    parser.add_argument('--max-tokens',type=int,required=True)
    parser.add_argument('--max-runtime-seconds',type=float,required=True)
    parser.add_argument('--concurrency',type=int,default=1)
    parser.add_argument('--output-tokens',type=int,default=64)
    parser.add_argument('--live',action='store_true',help='Send real requests (requires --confirm and Groq environment opt-in)')
    parser.add_argument('--confirm',action='store_true')
    parser.add_argument('--output-dir',type=Path,help='New directory for this run (default: unique timestamped directory)')
    args=parser.parse_args()
    config=LoadConfig(args.provider,args.scenario,args.max_requests,args.concurrency,args.max_tokens,args.max_runtime_seconds,args.output_tokens,not args.live,args.confirm)
    config.validate()
    print(json.dumps(config.summary(),indent=2),flush=True)
    if config.dry_run:
        print('Dry run: no provider requests sent.')
        return
    import uuid
    folder=args.output_dir or Path('results')/config.provider/str(uuid.uuid4())
    if folder.exists():
        raise ValueError('Output directory already exists; refusing to overwrite results')
    runner=LoadRunner(config)
    rows=asyncio.run(runner.run())
    write_csv(rows,folder/'results.csv',folder/'summary.csv')
    print(json.dumps(summarize(rows),indent=2))
    print('Stop reason:',runner.stop_reason,'Results:',folder)


if __name__=='__main__':
    main()
