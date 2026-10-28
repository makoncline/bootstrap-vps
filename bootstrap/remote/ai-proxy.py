#!/usr/bin/env python3
"""Configure Codex for CLIProxy, or check its model endpoint without credentials."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class ProxyError(ValueError):
    pass


def proxy_url(value):
    url = urllib.parse.urlsplit(value)
    host = url.hostname or ''
    try:
        address = ipaddress.ip_address(host)
        private = address.is_loopback or address in ipaddress.ip_network(
            '100.64.0.0/10' if address.version == 4 else 'fd7a:115c:a1e0::/48')
    except ValueError:
        private = host == 'localhost' or host.endswith('.ts.net')
    if (url.scheme not in ('http', 'https') or not private or url.username is not None
            or url.password is not None or url.query or url.fragment
            or url.path.rstrip('/') != '/v1' or any(c.isspace() for c in value)):
        raise ProxyError('Use a tailnet or loopback HTTP(S) URL ending in /v1, without credentials or query parameters')
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ProxyError('Use a valid proxy port')
    return value.rstrip('/')


def validate(url, model):
    if not url and not model:
        return None
    if not url or not model or any(c.isspace() for c in model):
        raise ProxyError('Set both AI_PROXY_URL and AI_PROXY_MODEL, or leave both blank')
    return proxy_url(url)


def configure(path, url, model):
    import tomlkit

    content = path.read_text() if path.exists() else ''
    config = tomlkit.parse(content)
    config['model_provider'] = 'cli_proxy'
    config['model'] = model
    providers = config.setdefault('model_providers', tomlkit.table())
    providers['cli_proxy'] = {
        'name': 'CLIProxy over Tailscale',
        'base_url': url,
        'wire_api': 'responses',
        'requires_openai_auth': False,
        'supports_websockets': False,
    }
    updated = tomlkit.dumps(config)
    if updated == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(updated)
            stream.flush()
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(path):
    import tomllib

    config = tomllib.loads(path.read_text())
    if config.get('model_provider') != 'cli_proxy':
        raise ProxyError('Codex does not have cli_proxy selected')
    provider = config['model_providers']['cli_proxy']
    url = proxy_url(provider['base_url'])
    model = config['model']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url + '/models', timeout=5) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ProxyError('Proxy model response exceeds 1 MiB')
            catalog = json.loads(data)
    except urllib.error.HTTPError as error:
        raise ProxyError(f'Proxy model check returned HTTP {error.code}') from None
    except urllib.error.URLError:
        raise ProxyError('Cannot reach the proxy model endpoint; check Tailscale, the listener, and tailnet grants') from None
    if not isinstance(catalog, dict) or not isinstance(catalog.get('data'), list):
        raise ProxyError('Proxy returned an invalid model catalog')
    if not any(isinstance(item, dict) and item.get('id') == model for item in catalog['data']):
        raise ProxyError('The configured model is absent from the proxy catalog')
    print(json.dumps({'status': 'reachable', 'url': url, 'model': model,
                      'inference_verified': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('validate', 'configure', 'check'))
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'config.toml')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            check(args.config)
        else:
            model = os.environ.get('AI_PROXY_MODEL', '')
            url = validate(os.environ.get('AI_PROXY_URL', ''), model)
            if args.command == 'configure' and url:
                configure(args.config, url, model)
    except ProxyError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OSError, KeyError, TypeError):
        # Configs and error responses can contain credentials. Keep them out of output.
        raise SystemExit('AI proxy check or configuration failed. Check the URL, model, config syntax, and tailnet access.') from None


if __name__ == '__main__':
    main()
