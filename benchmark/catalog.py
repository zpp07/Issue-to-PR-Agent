"""Twenty-two deterministic Python bug-fix cases with private hidden tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    issue: str
    category: str
    difficulty: str
    source: str
    public_tests: str
    hidden_tests: str


def _clean(text):
    """Remove the six-space call-site indentation while preserving Python blocks."""
    lines = text.splitlines()
    if not lines:
        return ""
    cleaned = [lines[0].lstrip()]
    cleaned.extend(line[6:] if line.startswith("      ") else line for line in lines[1:])
    return "\n".join(cleaned).rstrip() + "\n"


def c(id, issue, category, source, public, hidden, difficulty="easy"):
    return Case(id, issue, category, difficulty, _clean(source), _clean(public), _clean(hidden))


CASES = [
    c("case01_add", "Fix add so it returns the arithmetic sum.", "arithmetic",
      """def add(a, b):
          return a - b
      """, """from buggy import add
      def test_basic(): assert add(2, 3) == 5
      """, """from buggy import add
      def test_edges():
          assert add(-2, -3) == -5
          assert add(0, 0) == 0
      """),
    c("case02_grade", "Fix grade boundary ordering.", "branching",
      """def grade(score):
          if score >= 60: return "pass"
          if score >= 90: return "excellent"
          return "fail"
      """, """from buggy import grade
      def test_pass(): assert grade(70) == "pass"
      """, """from buggy import grade
      def test_boundaries():
          assert grade(100) == "excellent"
          assert grade(90) == "excellent"
          assert grade(59) == "fail"
      """),
    c("case03_average", "Raise ValueError for empty input and compute the mean.", "validation",
      """def average(values):
          return sum(values) / len(values)
      """, """from buggy import average
      def test_values(): assert average([1, 2, 3]) == 2
      """, """import pytest
      from buggy import average
      def test_empty():
          with pytest.raises(ValueError): average([])
      def test_single(): assert average([7]) == 7
      """),
    c("case04_median", "Compute even medians and do not mutate the caller list.", "collections",
      """def median(values):
          values.sort()
          return values[len(values) // 2]
      """, """from buggy import median
      def test_odd(): assert median([3, 1, 2]) == 2
      """, """from buggy import median
      def test_even(): assert median([1, 2, 3, 4]) == 2.5
      def test_no_mutation():
          data = [3, 1, 2]; median(data); assert data == [3, 1, 2]
      """, "medium"),
    c("case05_clamp", "Clamp values inclusively between lower and upper bounds.", "branching",
      """def clamp(value, lower, upper):
          return max(upper, min(lower, value))
      """, """from buggy import clamp
      def test_middle(): assert clamp(5, 0, 10) == 5
      """, """from buggy import clamp
      def test_limits():
          assert clamp(-1, 0, 10) == 0
          assert clamp(11, 0, 10) == 10
          assert clamp(0, 0, 10) == 0
      """),
    c("case06_slugify", "Normalize text to lowercase hyphen-separated slugs.", "strings",
      """def slugify(text):
          return text.replace(" ", "-")
      """, """from buggy import slugify
      def test_simple(): assert slugify("Hello World") == "hello-world"
      """, """from buggy import slugify
      def test_whitespace():
          assert slugify("  Hello   WORLD  ") == "hello-world"
      def test_empty(): assert slugify("   ") == ""
      """, "medium"),
    c("case07_parse_bool", "Parse common boolean strings and reject unknown values.", "parsing",
      """def parse_bool(value):
          return bool(value)
      """, """from buggy import parse_bool
      def test_true(): assert parse_bool("true") is True
      """, """import pytest
      from buggy import parse_bool
      def test_false_values():
          assert parse_bool("false") is False
          assert parse_bool("0") is False
      def test_case_and_invalid():
          assert parse_bool(" YES ") is True
          with pytest.raises(ValueError): parse_bool("maybe")
      """, "medium"),
    c("case08_chunks", "Reject non-positive sizes and split a sequence into complete final chunks.", "collections",
      """def chunks(values, size):
          return [values[i:i+size] for i in range(0, len(values) - 1, size)]
      """, """from buggy import chunks
      def test_even(): assert chunks([1,2,3,4], 2) == [[1,2],[3,4]]
      """, """import pytest
      from buggy import chunks
      def test_remainder(): assert chunks([1,2,3,4,5], 2) == [[1,2],[3,4],[5]]
      def test_bad_size():
          with pytest.raises(ValueError): chunks([1], 0)
      """, "medium"),
    c("case09_dedupe", "Remove duplicates while preserving first-seen order.", "collections",
      """def dedupe(values):
          return list(set(values))
      """, """from buggy import dedupe
      def test_unique(): assert dedupe([1, 2, 3]) == [1, 2, 3]
      """, """from buggy import dedupe
      def test_order(): assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]
      def test_empty(): assert dedupe([]) == []
      """),
    c("case10_moving_average", "Return all moving averages; raise ValueError unless 1 <= window <= len(values).", "algorithms",
      """def moving_average(values, window):
          return [sum(values[i:i+window]) / len(values) for i in range(len(values)-window)]
      """, """from buggy import moving_average
      def test_basic(): assert moving_average([1,2,3], 2) == [1.5, 2.5]
      """, """import pytest
      from buggy import moving_average
      def test_full_window(): assert moving_average([2,4,6], 3) == [4]
      def test_invalid():
          with pytest.raises(ValueError): moving_average([1], 0)
          with pytest.raises(ValueError): moving_average([1], 2)
      """, "medium"),
    c("case11_paginate", "Reject non-positive page/size and use one-based pages without skipping the first.", "indexing",
      """def paginate(values, page, size):
          start = page * size
          return values[start:start+size]
      """, """from buggy import paginate
      def test_second(): assert paginate([1,2,3,4], 2, 2) == [3,4]
      """, """import pytest
      from buggy import paginate
      def test_first(): assert paginate([1,2,3], 1, 2) == [1,2]
      def test_invalid():
          with pytest.raises(ValueError): paginate([1], 0, 1)
          with pytest.raises(ValueError): paginate([1], 1, 0)
      """),
    c("case12_safe_divide", "Return the default only for division by zero.", "exceptions",
      """def safe_divide(a, b, default=None):
          try: return a / b
          except TypeError: return default
      """, """from buggy import safe_divide
      def test_normal(): assert safe_divide(6, 3) == 2
      """, """import pytest
      from buggy import safe_divide
      def test_zero(): assert safe_divide(1, 0, "n/a") == "n/a"
      def test_type_errors_propagate():
          with pytest.raises(TypeError): safe_divide("1", 1)
      """),
    c("case13_flatten", "Flatten exactly one nesting level without splitting strings.", "collections",
      """def flatten(groups):
          result = []
          for group in groups:
              result += group
          return result
      """, """from buggy import flatten
      def test_lists(): assert flatten([[1,2],[3]]) == [1,2,3]
      """, """from buggy import flatten
      def test_scalar_strings(): assert flatten(["ab", ["cd"]]) == ["ab", "cd"]
      def test_empty(): assert flatten([]) == []
      """),
    c("case14_fibonacci", "Implement Fibonacci with F(0)=0 and reject negatives.", "algorithms",
      """def fibonacci(n):
          if n <= 1: return 1
          return fibonacci(n-1) + fibonacci(n-2)
      """, """from buggy import fibonacci
      def test_two(): assert fibonacci(2) == 1
      """, """import pytest
      from buggy import fibonacci
      def test_base():
          assert fibonacci(0) == 0
          assert fibonacci(1) == 1
      def test_negative():
          with pytest.raises(ValueError): fibonacci(-1)
      """, "medium"),
    c("case15_merge", "Return a merged config without mutating defaults.", "state",
      """def merge_config(defaults, overrides):
          defaults.update(overrides)
          return defaults
      """, """from buggy import merge_config
      def test_merge(): assert merge_config({"a":1},{"b":2}) == {"a":1,"b":2}
      """, """from buggy import merge_config
      def test_no_mutation():
          base={"a":1}; merge_config(base,{"a":2}); assert base == {"a":1}
      def test_override(): assert merge_config({"a":1},{"a":3})["a"] == 3
      """),
    c("case16_top_k", "Reject negative k and return the largest k values without mutating input.", "algorithms",
      """def top_k(values, k):
          values.sort()
          return values[:k]
      """, """from buggy import top_k
      def test_basic(): assert top_k([1,3,2], 2) == [3,2]
      """, """import pytest
      from buggy import top_k
      def test_no_mutation():
          data=[3,1,2]; top_k(data,2); assert data == [3,1,2]
      def test_invalid():
          with pytest.raises(ValueError): top_k([1], -1)
      """),
    c("case17_percentage", "Return a percentage and raise ValueError when total is zero.", "arithmetic",
      """def percentage(part, total):
          return part / total
      """, """from buggy import percentage
      def test_half(): assert percentage(1, 2) == 50
      """, """import pytest
      from buggy import percentage
      def test_full(): assert percentage(5, 5) == 100
      def test_zero():
          with pytest.raises(ValueError): percentage(1, 0)
      """),
    c("case18_normalize", "Min-max normalize values and map a constant series to all zeros.", "data",
      """def normalize(values):
          high = max(values)
          return [value / high for value in values]
      """, """from buggy import normalize
      def test_zero_based(): assert normalize([0,5,10]) == [0,0.5,1]
      """, """from buggy import normalize
      def test_offset(): assert normalize([10,15,20]) == [0,0.5,1]
      def test_constant(): assert normalize([4,4]) == [0,0]
      """, "medium"),
    c("case19_retry_delays", "Reject negative attempts and generate capped exponential backoff delays.", "algorithms",
      """def retry_delays(attempts, base=1, cap=60):
          return [min(cap, base * i) for i in range(attempts)]
      """, """from buggy import retry_delays
      def test_three(): assert retry_delays(3) == [1,2,4]
      """, """import pytest
      from buggy import retry_delays
      def test_cap(): assert retry_delays(5, base=2, cap=10) == [2,4,8,10,10]
      def test_invalid():
          with pytest.raises(ValueError): retry_delays(-1)
      """, "medium"),
    c("case20_group_by", "Group records by key in order; raise KeyError when a record lacks it.", "data",
      """def group_by(records, key):
          result = {}
          for record in records:
              result[record.get(key)] = record
          return result
      """, """from buggy import group_by
      def test_single(): assert group_by([{"team":"a","id":1}],"team")["a"] == [{"team":"a","id":1}]
      """, """import pytest
      from buggy import group_by
      def test_multiple():
          rows=[{"k":1,"v":"a"},{"k":1,"v":"b"}]
          assert group_by(rows,"k")[1] == rows
      def test_missing():
          with pytest.raises(KeyError): group_by([{}],"k")
      """, "medium"),
    c("case21_is_prime", "Correctly identify prime numbers including square boundaries.", "algorithms",
      """def is_prime(n):
          if n < 1: return False
          for divisor in range(2, int(n ** 0.5)):
              if n % divisor == 0: return False
          return True
      """, """from buggy import is_prime
      def test_known(): assert is_prime(7) is True
      """, """from buggy import is_prime
      def test_boundaries():
          assert is_prime(1) is False
          assert is_prime(2) is True
          assert is_prime(4) is False
          assert is_prime(49) is False
      """),
    c("case22_transpose", "Transpose rectangular matrices and reject ragged input.", "data",
      """def transpose(matrix):
          return [[row[i] for row in matrix] for i in range(len(matrix))]
      """, """from buggy import transpose
      def test_square(): assert transpose([[1,2],[3,4]]) == [[1,3],[2,4]]
      """, """import pytest
      from buggy import transpose
      def test_rectangular(): assert transpose([[1,2,3],[4,5,6]]) == [[1,4],[2,5],[3,6]]
      def test_empty(): assert transpose([]) == []
      def test_ragged():
          with pytest.raises(ValueError): transpose([[1],[2,3]])
      """, "medium"),
]


CASE_BY_ID = {case.id: case for case in CASES}
assert len(CASES) >= 20 and len(CASE_BY_ID) == len(CASES)
