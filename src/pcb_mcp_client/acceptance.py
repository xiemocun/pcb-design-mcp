"""Evaluate explicitly declared report criteria, without executing expressions."""
import math
import re
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .plan import Strict

REPORTS = ('spec_validate', 'bom_check', 'electrical_check', 'power_budget_check',
           'schematic_verify', 'erc_run', 'pcb_verify', 'drc_run', 'pcb_layout_check', 'pcb_copper_check','functional_check')


class Criterion(Strict):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    report: Literal['spec_validate', 'bom_check', 'electrical_check', 'power_budget_check',
                    'schematic_verify', 'erc_run', 'pcb_verify', 'drc_run', 'pcb_layout_check', 'pcb_copper_check','functional_check']
    pointer: Annotated[str, Field(pattern=r'^/', max_length=500)]
    operator: Literal['equals', 'minimum', 'maximum']
    expected: str | float | int | bool

    @model_validator(mode='after')
    def valid(self):
        if re.search(r'~(?![01])', self.pointer):
            raise ValueError('INVALID_JSON_POINTER')
        if isinstance(self.expected, float) and not math.isfinite(self.expected):
            raise ValueError('NONFINITE_EXPECTATION')
        if self.operator != 'equals' and (isinstance(self.expected, bool) or not isinstance(self.expected, (float, int))):
            raise ValueError('NUMERIC_EXPECTATION_REQUIRED')
        return self


def criteria(requirement):
    raw = requirement.get('software_acceptance', []) if isinstance(requirement, dict) else []
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError('INVALID_SOFTWARE_ACCEPTANCE')
    result = [Criterion.model_validate(x) for x in raw]
    if len({x.id for x in result}) != len(result):
        raise ValueError('DUPLICATE_CRITERION_ID')
    return result


def delivery_criteria():
    verdicts = ('spec_validate', 'functional_check', 'schematic_verify', 'erc_run',
                'pcb_verify', 'pcb_layout_check', 'drc_run')
    return [
        *(Criterion(id=report, report=report, pointer='/verdict', operator='equals', expected='pass')
          for report in verdicts),
        Criterion(id='no_unconnected', report='drc_run', pointer='/details/unconnected_count',
                  operator='equals', expected=0),
        Criterion(id='copper_geometry', report='pcb_copper_check', pointer='/geometry_verdict',
                  operator='equals', expected='pass'),
    ]


def evaluate(requested, reports):
    results = []
    for criterion in requested:
        item = dict(criterion.model_dump(), verdict='incomplete')
        try:
            actual = reports[criterion.report]
            for token in criterion.pointer.split('/')[1:]:
                token = token.replace('~1', '/').replace('~0', '~')
                if isinstance(actual, list):
                    if not re.fullmatch(r'0|[1-9][0-9]*', token):
                        raise KeyError(token)
                    actual = actual[int(token)]
                else:
                    actual = actual[token]
            if isinstance(actual, float) and not math.isfinite(actual):
                raise ValueError('NONFINITE_REPORT_VALUE')
            item['actual'] = actual
            if criterion.operator == 'equals':
                numeric = lambda x: type(x) in (int, float) and math.isfinite(x)
                good = (type(actual) is type(criterion.expected) or numeric(actual) and numeric(criterion.expected)) and actual == criterion.expected
            else:
                if type(actual) not in (int, float) or not math.isfinite(actual):
                    raise TypeError('REPORT_VALUE_NOT_FINITE_NUMBER')
                good = actual >= criterion.expected if criterion.operator == 'minimum' else actual <= criterion.expected
            item['verdict'] = 'pass' if good else 'fail'
        except (KeyError, IndexError, TypeError, ValueError):
            item['reason'] = 'REPORT_OR_VALUE_UNAVAILABLE'
        results.append(item)
    verdict = 'fail' if any(x['verdict'] == 'fail' for x in results) else ('pass' if results and all(x['verdict'] == 'pass' for x in results) else 'incomplete')
    return dict(verdict=verdict, criteria=results, scope='declared_report_criteria_only',
                full_function_qualified=False, physical_verified=False,
                note='Unmapped prose requirements and physical behavior are not established by these comparisons.')
