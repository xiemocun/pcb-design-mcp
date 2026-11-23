"""Small read-only S-expression parser retaining source spans for exact library copies."""
import json
import re


def parse(text):
    tokens = list(re.finditer(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text))

    def node(i):
        if tokens[i].group() != '(':
            raise ValueError('Expected list')
        start = tokens[i].start()
        values = []
        i += 1
        while tokens[i].group() != ')':
            if tokens[i].group() == '(':
                value, i = node(i)
                values.append(value)
            else:
                token = tokens[i].group()
                values.append(json.loads(token) if token.startswith('"') else token)
                i += 1
        return {'v': values, 'start': start, 'end': tokens[i].end()}, i + 1

    result, end = node(0)
    if end != len(tokens):
        raise ValueError('Trailing input')
    return result


def children(node, key):
    return [v for v in node['v'] if isinstance(v, dict) and v['v'][0] == key]


def one(node, key):
    return children(node, key)[0]['v'][1:]
