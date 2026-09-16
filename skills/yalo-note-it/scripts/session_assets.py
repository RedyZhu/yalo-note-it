"""Preserve local temporary references without rewriting source records."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlparse

from codex_format import ArchiveError


@dataclass(frozen=True)
class Asset:
    source: Path
    record_id: str
    sha256: str


def temporary_roots(home):
    roots = {Path(tempfile.gettempdir()).resolve(), home / 'tmp', home / 'clipboard',
             home / 'generated_images', home / 'visualizations'}
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        if os.environ.get(key):
            roots.add(Path(os.environ[key]).resolve())
    return roots


def local_path(value):
    value = value.strip().strip('<>')
    if value.startswith('file://'):
        parsed = urlparse(value)
        if parsed.netloc not in ('', 'localhost'):
            raise ArchiveError('Unsupported network file attachment')
        value = unquote(parsed.path)
        if re.match(r'^/[A-Za-z]:/', value):
            value = value[1:]
    # Markdown file links may carry a line number.
    value = re.sub(r':\d+$', '', value)
    path = Path(value)
    return path if path.is_absolute() else None


def text_references(text):
    # Markdown presents files to the conversation. Incidental paths inside code,
    # tracebacks and diagnostics are not attachment declarations.
    for match in re.finditer(r'!?\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)', text):
        path = local_path(match.group(1))
        if path:
            yield path


def references(block):
    if block['type'] == 'input_image':
        url = block['image_url']
        if url.startswith('data:'):
            return
        path = local_path(url)
        if path is None:
            raise ArchiveError('Non-embedded image cannot be reliably preserved: ' + url[:160])
        yield path
    else:
        text = block['text']
        # Tool output sometimes contains JSON; decode it before examining its strings.
        try:
            value = json.loads(text)
        except ValueError:
            value = text
        yield from value_references(value)


def value_references(value):
    if isinstance(value, str):
        path = local_path(value)
        if path is not None and '\n' not in value.strip():
            yield path
        else:
            yield from text_references(value)
    elif isinstance(value, list):
        for item in value:
            yield from value_references(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from value_references(item)


def file_hash(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def plan_assets(selected, home):
    roots = temporary_roots(home)
    assets = {}
    for _, row in selected:
        p = row['payload']
        if p.get('type') == 'message':
            blocks = p['content']
        elif p.get('type') == 'custom_tool_call_output':
            blocks = p['output']
        elif p.get('type') == 'function_call_output':
            output = p['output']
            if isinstance(output, str):
                blocks = [{'type': 'input_text', 'text': output}]
            elif isinstance(output, list):
                blocks = output
            else:
                raise ArchiveError('Unsupported function output')
        else:
            continue
        for block in blocks:
            for path in references(block):
                absolute = Path(os.path.abspath(path))
                resolved = path.resolve()
                if not any(root == absolute or root in absolute.parents or
                           root == resolved or root in resolved.parents for root in roots):
                    continue
                if resolved.is_dir():
                    continue
                if not resolved.is_file():
                    raise ArchiveError('Necessary temporary attachment missing; original reference retained in source: ' + str(path))
                if resolved not in assets:
                    assets[resolved] = Asset(resolved, p['id'], file_hash(resolved))
    return list(assets.values())


def copy_assets(target, assets, created):
    if not assets:
        return
    folder = target / 'assets'
    folder.mkdir(exist_ok=True)
    for asset in assets:
        data = asset.source.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ArchiveError('Attachment changed while archiving: ' + str(asset.source))
        destination = folder / asset.source.name
        if destination.exists() and file_hash(destination) != asset.sha256:
            suffix = re.sub(r'[^A-Za-z0-9_-]', '_', asset.record_id)
            destination = folder / (asset.source.stem + '-' + suffix + asset.source.suffix)
        if destination.exists():
            if file_hash(destination) != asset.sha256:
                raise ArchiveError('Attachment name collision cannot be resolved: ' + str(destination))
            continue
        # Exclusive creation protects existing assets even when names collide.
        with destination.open('xb') as stream:
            created.append(destination)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if file_hash(destination) != asset.sha256:
            raise ArchiveError('Attachment copy verification failed')
