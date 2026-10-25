"""Shared navigation and HTML template rendering."""
import html
import re

from ..core.common import ROOT, SCALES


def report_name(scale):
    return 'report.html' if scale == '1GB' else f'report_{scale}.html'


def navigation(active=None):
    links = [('Home', 'index.html'), ('Compare sizes', 'comparison.html')]
    links += [(scale, report_name(scale)) for scale in SCALES]
    return '<nav class="toolbar" aria-label="Site navigation">' + ''.join(
        '<a href="' + target + '"' + (' aria-current="page"' if target == active else '') + '>' + label + '</a>'
        for label, target in links) + '</nav>'


def template(name, values):
    text = (ROOT / 'web' / 'templates' / name).read_text(encoding='utf-8')
    return re.sub(r'<!--__(\w+)__-->', lambda match: values[match[1]], text)


def pending_report(scale, errors=()):
    status = ('Input files could not be fully analyzed.' if errors else
              'No execution plans have been contributed for this size yet.')
    detail = ('<pre>' + html.escape('\n'.join(errors)) + '</pre>') if errors else ''
    return template('pending.html', dict(SCALE=scale, NAV=navigation(report_name(scale)),
                                       STATUS=status, ERRORS=detail))
