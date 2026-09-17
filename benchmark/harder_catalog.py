"""Cross-file cases whose contracts live outside the buggy implementation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardCase:
    id: str
    issue: str
    category: str
    files: dict[str, str]
    public_tests: str
    hidden_tests: str
    relevant_files: tuple[str, ...]


CASES = [
    HardCase(
        "hard01_discount_contract",
        "Checkout totals violate the documented VIP discount tiers, especially at boundaries. Fix the implementation.",
        "docs-and-config",
        {
            "shop/__init__.py": "",
            "shop/rules.py": "VIP_TIERS = [(100, 0.05), (500, 0.10), (1000, 0.15)]\n",
            "shop/pricing.py": '''from .rules import VIP_TIERS

def checkout_total(subtotal, vip=False):
    if subtotal < 0:
        raise ValueError("subtotal must be non-negative")
    if not vip:
        return round(subtotal, 2)
    return round(subtotal * 0.9, 2)
''',
            "shop/coupons.py": "def apply_coupon(total, amount):\n    return max(0, total - amount)\n",
            "docs/discounts.md": "# VIP pricing\nApply the highest tier whose threshold is less than or equal to the subtotal. Below 100 there is no VIP discount. Thresholds and rates are defined in `shop.rules.VIP_TIERS`.\n",
        },
        '''from shop.pricing import checkout_total

def test_regular_and_mid_vip():
    assert checkout_total(200, False) == 200
    assert checkout_total(200, True) == 190
''',
        '''from shop.pricing import checkout_total

def test_boundaries_and_top_tier():
    assert checkout_total(99.99, True) == 99.99
    assert checkout_total(100, True) == 95
    assert checkout_total(500, True) == 450
    assert checkout_total(1000, True) == 850
''',
        ("docs/discounts.md", "shop/rules.py", "shop/pricing.py"),
    ),
    HardCase(
        "hard02_schema_aliases",
        "Incoming customer rows are not normalized according to the schema contract. Repair normalization without changing tests.",
        "data-pipeline",
        {
            "pipeline/__init__.py": "",
            "pipeline/schema.py": "ALIASES = {'customer_id': ('customer_id', 'id', 'customerId'), 'email': ('email', 'email_address')}\nREQUIRED = ('customer_id', 'email')\n",
            "pipeline/normalize.py": '''from .schema import REQUIRED

def normalize_customer(row):
    result = {key: row.get(key) for key in REQUIRED}
    return result
''',
            "pipeline/export.py": "def export_rows(rows):\n    return list(rows)\n",
            "docs/schema.md": "# Customer schema\nFor every canonical field, accept the first present alias in `pipeline.schema.ALIASES`. Strip surrounding whitespace from string values. Raise `ValueError` naming any missing required canonical field.\n",
        },
        '''from pipeline.normalize import normalize_customer

def test_canonical_fields():
    assert normalize_customer({'customer_id': ' 7 ', 'email': ' a@b.com '}) == {'customer_id': '7', 'email': 'a@b.com'}
''',
        '''import pytest
from pipeline.normalize import normalize_customer

def test_aliases_and_missing():
    assert normalize_customer({'customerId': '9', 'email_address': 'x@y.com'}) == {'customer_id': '9', 'email': 'x@y.com'}
    with pytest.raises(ValueError, match='email'):
        normalize_customer({'id': '1'})
''',
        ("docs/schema.md", "pipeline/schema.py", "pipeline/normalize.py"),
    ),
    HardCase(
        "hard03_role_inheritance",
        "Permission checks ignore role inheritance and deny valid actions. Implement the policy described by the project.",
        "service-policy",
        {
            "auth/__init__.py": "",
            "auth/roles.py": "PARENTS = {'admin': 'editor', 'editor': 'viewer', 'viewer': None}\nPERMISSIONS = {'viewer': {'read'}, 'editor': {'write'}, 'admin': {'delete'}}\n",
            "auth/policy.py": '''from .roles import PERMISSIONS

def can(role, action):
    return action in PERMISSIONS.get(role, set())
''',
            "auth/session.py": "def authenticated(user):\n    return bool(user)\n",
            "docs/authorization.md": "# Role inheritance\nRoles inherit all permissions from their parent recursively. Unknown roles have no permissions. Parent links live in `auth.roles.PARENTS`; cycles must not loop forever.\n",
        },
        '''from auth.policy import can

def test_direct_permission():
    assert can('viewer', 'read')
    assert can('editor', 'write')
''',
        '''from auth.policy import can

def test_inherited_and_unknown():
    assert can('editor', 'read')
    assert can('admin', 'read')
    assert can('admin', 'write')
    assert not can('unknown', 'read')
''',
        ("docs/authorization.md", "auth/roles.py", "auth/policy.py"),
    ),
    HardCase(
        "hard04_retry_units",
        "Retry delays are dramatically longer than the configuration contract specifies. Fix delay calculation and bounds.",
        "configuration",
        {
            "worker/__init__.py": "",
            "worker/config.py": "DEFAULT_BASE_MS = 100\nMAX_DELAY_MS = 5000\n",
            "worker/backoff.py": '''from .config import DEFAULT_BASE_MS, MAX_DELAY_MS

def retry_delay(attempt, base_ms=DEFAULT_BASE_MS):
    if attempt < 0:
        raise ValueError("attempt")
    return min(base_ms * (2 ** attempt), MAX_DELAY_MS)
''',
            "worker/queue.py": "def pending(items):\n    return len(items)\n",
            "docs/retries.md": "# Retry API\n`retry_delay` returns seconds as a float although configuration is expressed in milliseconds. Apply the maximum in milliseconds before converting. Attempt zero equals the base delay.\n",
        },
        '''from worker.backoff import retry_delay

def test_initial_delay():
    assert retry_delay(0) == 0.1
''',
        '''import pytest
from worker.backoff import retry_delay

def test_growth_cap_and_invalid():
    assert retry_delay(3, 250) == 2.0
    assert retry_delay(20) == 5.0
    with pytest.raises(ValueError): retry_delay(-1)
''',
        ("docs/retries.md", "worker/config.py", "worker/backoff.py"),
    ),
    HardCase(
        "hard05_merge_precedence",
        "Layered application settings use the wrong precedence and mutate caller input. Align them with the documented contract.",
        "configuration",
        {
            "settings/__init__.py": "",
            "settings/defaults.py": "DEFAULTS = {'timeout': 30, 'retries': 2, 'region': 'local'}\n",
            "settings/merge.py": '''from .defaults import DEFAULTS

def build_settings(file_values, env_values):
    file_values.update(DEFAULTS)
    file_values.update(env_values)
    return file_values
''',
            "settings/validate.py": "def valid_timeout(value):\n    return value > 0\n",
            "docs/configuration.md": "# Precedence\nCreate a new mapping. Precedence from lowest to highest is defaults, config file, then environment. Inputs must remain unchanged. Values explicitly set to `None` mean unset and should fall back to the lower layer.\n",
        },
        '''from settings.merge import build_settings

def test_precedence():
    assert build_settings({'timeout': 20}, {'timeout': 10})['timeout'] == 10
''',
        '''from settings.merge import build_settings

def test_fallback_and_no_mutation():
    file_values = {'timeout': 20, 'region': 'eu'}
    env_values = {'timeout': None, 'retries': 5}
    assert build_settings(file_values, env_values) == {'timeout': 20, 'retries': 5, 'region': 'eu'}
    assert file_values == {'timeout': 20, 'region': 'eu'}
    assert env_values == {'timeout': None, 'retries': 5}
''',
        ("docs/configuration.md", "settings/defaults.py", "settings/merge.py"),
    ),
    HardCase(
        "hard06_null_serialization",
        "API payload serialization leaks unset fields and mishandles aliases. Implement the public wire-format contract.",
        "api-contract",
        {
            "api/__init__.py": "",
            "api/fields.py": "ALIASES = {'user_id': 'userId', 'display_name': 'displayName'}\n",
            "api/serialize.py": '''from .fields import ALIASES

def to_payload(values):
    return {ALIASES.get(key, key): value for key, value in values.items()}
''',
            "api/client.py": "def endpoint(resource):\n    return '/v1/' + resource\n",
            "docs/wire-format.md": "# JSON wire format\n+Omit fields whose value is `None`, preserve false/zero/empty-string values, and translate known snake_case fields using `api.fields.ALIASES`. Unknown keys remain unchanged. Never mutate input.\n",
        },
        '''from api.serialize import to_payload

def test_alias():
    assert to_payload({'user_id': 7}) == {'userId': 7}
''',
        '''from api.serialize import to_payload

def test_none_and_falsy_values():
    values = {'display_name': None, 'enabled': False, 'count': 0, 'note': ''}
    assert to_payload(values) == {'enabled': False, 'count': 0, 'note': ''}
    assert values['display_name'] is None
''',
        ("docs/wire-format.md", "api/fields.py", "api/serialize.py"),
    ),
]

CASE_BY_ID = {case.id: case for case in CASES}
