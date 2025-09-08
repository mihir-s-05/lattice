#!/usr/bin/env python3
import argparse
import json

def main(argv=None):
    p = argparse.ArgumentParser(prog='small-cli', description='Small CLI example')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list', help='List items')
    pc = sub.add_parser('create', help='Create item')
    pc.add_argument('--name', required=True)
    pc.add_argument('--description', default='')
    pg = sub.add_parser('get', help='Get item')
    pg.add_argument('id')
    pu = sub.add_parser('update', help='Update item')
    pu.add_argument('id')
    pu.add_argument('--name')
    pu.add_argument('--description')
    pd = sub.add_parser('delete', help='Delete item')
    pd.add_argument('id')
    args = p.parse_args(argv)
    if args.cmd == 'list':
        print(json.dumps({'items': []}))
    elif args.cmd == 'create':
        print(json.dumps({'id': '1', 'name': args.name, 'description': args.description}))
    elif args.cmd == 'get':
        print(json.dumps({'id': args.id, 'name': 'demo', 'description': ''}))
    elif args.cmd == 'update':
        print(json.dumps({'id': args.id, 'name': args.name or 'demo', 'description': args.description or ''}))
    elif args.cmd == 'delete':
        print(json.dumps({'deleted': args.id}))

if __name__ == '__main__':
    main()