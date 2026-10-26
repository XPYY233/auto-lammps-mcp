"""Render the 45 original review findings with concrete evidence and remaining outcomes."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def render():
    data = json.loads((ROOT / 'docs/review-traceability.json').read_text())
    findings = data['findings']
    assert len(findings) == len({f['id'] for f in findings}) == 45
    lines = ['# 审核意见追踪', '',
             '保留四轮审核中的 45 条原始意见；已验证结果与下一项用户验收分别列出。原始要求、验收条件、实现与检查文件见 [完整记录](review-traceability.json)。', '',
             '| 编号 | 要求 | 已有结果 | 下一项验收 | 证据 |',
             '|---|---|---|---|---|']
    for item in findings:
        for path in item['implementation'] + item['checks']:
            assert (ROOT / path).is_file(), (item['id'], path)
        assert item['observed'] and item['remaining']
        evidence = []
        for reference in item['evidence']:
            if reference.startswith('https://github.com/XPYY233/auto-lammps-mcp/'):
                evidence.append('[开发记录](' + reference + ')')
            else:
                assert (ROOT / reference).is_file(), reference
                evidence.append('[验证记录](' + str(Path(reference).relative_to('docs')) + ')')
        cells = [item['id'], item['title'], item['observed'], item['remaining'], ' · '.join(evidence) or '待形成对应领域记录']
        lines.append('| ' + ' | '.join(c.replace('|', '\\|').replace('\n', ' ') for c in cells) + ' |')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    expected = render()
    path = ROOT / 'docs/REVIEW_TRACEABILITY.md'
    if options.check:
        assert path.read_text() == expected, 'Run scripts/render_review_traceability.py'
        print('45 review findings: references and generated register match')
    else:
        path.write_text(expected)
