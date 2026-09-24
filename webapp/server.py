#!/usr/bin/python3
"""Local web browser for PyMEOP event files.

Serves the single page app in static/ and a small read only JSON API over the
event files in the data directory. Built on the standard library so it runs in
the same environment as the DAQ application with nothing extra installed.

    python webapp/server.py [--port 8000] [--host 127.0.0.1] [--no-browser]
                            [--data-dir DIR]
"""

import argparse
import json
import mimetypes
import os
import posixpath
import sys
import threading
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp import dataset, fitting

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

LIBRARY = dataset.Library()


class Handler(SimpleHTTPRequestHandler):
    '''Routes /api/ to the event files and everything else to static/'''

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        if path.startswith('/api/'):
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                self.api(path[len('/api/'):].strip('/'), query)
            except BrokenPipeError:
                pass  # the browser navigated away mid response
            except ValueError as e:  # a folder that is not there or not allowed
                self.send_json({'error': str(e)}, status=400)
            except Exception as e:
                self.send_json({'error': str(e)}, status=500)
            return
        self.static(path)

    def do_POST(self):
        '''Computations on data the page sends; nothing here touches the files'''

        path = unquote(urlparse(self.path).path)
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > 10 * 1024 * 1024:
                self.send_json({'error': 'Request too large'}, status=413)
                return
            body = json.loads(self.rfile.read(length) or b'{}')
            if path == '/api/fit/exp':
                asym = body.get('asymptote')
                self.send_json(fitting.exp_fit(body['t'], body['y'], body.get('sigma'),
                                               None if asym is None else float(asym)))
                return
            self.send_json({'error': 'Unknown route'}, status=404)
        except BrokenPipeError:
            pass
        except (ValueError, KeyError, TypeError) as e:
            self.send_json({'error': str(e)}, status=400)
        except Exception as e:
            self.send_json({'error': str(e)}, status=500)

    def api(self, route, query):
        '''Answer one API route

        Args:
            route: Path below /api/, already unquoted
            query: Query string values; dir picks the folder, default config.yaml's
        '''

        parts = [p for p in route.split('/') if p]

        if parts == ['folders']:
            self.send_json(dataset.folders(query.get('path')))
            return

        base = dataset.resolve_dir(query.get('dir'))

        if parts == ['files']:
            # fingerprint before reading, so a write that lands in between shows
            # up as a changed version on the next poll rather than being missed
            version = LIBRARY.fingerprint(base)
            self.send_json({'data_dir': base, 'default_dir': dataset.data_dir(),
                            'files': LIBRARY.index(base), 'version': version})
            return

        if parts == ['progress']:
            self.send_json(dict(LIBRARY.progress(base), path=base))
            return

        if parts == ['version']:
            self.send_json({'version': LIBRARY.fingerprint(base)})
            return

        if len(parts) == 2 and parts[0] == 'file':
            run = LIBRARY.run(parts[1], base)
            if not run:
                self.send_json({'error': 'No such event file'}, status=404)
                return
            self.send_json(run.detail())
            return

        if len(parts) == 4 and parts[0] == 'file' and parts[2] == 'event':
            run = LIBRARY.run(parts[1], base)
            if not run:
                self.send_json({'error': 'No such event file'}, status=404)
                return
            try:
                event = run.events[int(parts[3])]
            except (ValueError, IndexError):
                self.send_json({'error': 'No such scan in this file'}, status=404)
                return
            self.send_json(event.detail())
            return

        if len(parts) == 3 and parts[0] == 'file' and parts[2] == 'raw':
            run = LIBRARY.run(parts[1], base)
            if not run:
                self.send_json({'error': 'No such event file'}, status=404)
                return
            self.send_file(run.path, 'text/plain; charset=utf-8')
            return

        self.send_json({'error': 'Unknown route'}, status=404)

    def static(self, path):
        '''Serve the single page app, defaulting to index.html'''

        rel = posixpath.normpath(path).lstrip('/')
        if not rel or rel == '.':
            rel = 'index.html'
        target = os.path.realpath(os.path.join(STATIC, rel))
        if not target.startswith(os.path.realpath(STATIC)) or not os.path.isfile(target):
            self.send_json({'error': 'Not found'}, status=404)
            return
        ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        self.send_file(target, ctype)

    def send_file(self, path, ctype):
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        '''Quieter than the default, which logs every static asset'''

        if '/api/' in (self.path or ''):
            sys.stderr.write('  %s\n' % (fmt % args))


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='127.0.0.1', help='interface to bind, default localhost only')
    parser.add_argument('--port', type=int, default=8000, help='port to listen on')
    parser.add_argument('--no-browser', action='store_true', help='do not open a browser window')
    parser.add_argument('--data-dir', help='folder to open first, default event_dir in config.yaml')
    args = parser.parse_args()

    if args.data_dir:
        if not os.path.isdir(args.data_dir):
            parser.error('no such folder: %s' % args.data_dir)
        dataset.DEFAULT_DIR = os.path.abspath(args.data_dir)
    # Anyone who can reach the server can open any folder it allows, so off the
    # local machine keep the folder picker inside the default data directory
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        dataset.FOLDER_LIMIT = dataset.data_dir()

    server = Server((args.host, args.port), Handler)
    url = 'http://%s:%d/' % ('localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host,
                             server.server_address[1])
    base = dataset.data_dir()
    n = len(LIBRARY.names(base))
    print('PyMEOP data browser')
    print('  data   %s (%d event files)' % (base, n))
    if dataset.FOLDER_LIMIT:
        print('  other folders limited to ones inside it, as the server is reachable off this machine')
    print('  serving %s   (ctrl-c to stop)' % url)
    # start reading the default folder now, so the page has less to wait for; the
    # page's own request joins this pass and shows its progress
    threading.Thread(target=LIBRARY.index, args=(base,), daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
