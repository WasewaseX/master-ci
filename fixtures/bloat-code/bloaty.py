"""Fixture module with structural-budget violations only.

Deliberately over-commented, over-parameterized and over-long so the
detector's structural-budget family has something to report. Every
function body is real code: no stubs, no swallowed exceptions, no
empty tests - the blocking checks must stay silent.
"""

# ========================================
#   section: value helpers
# ========================================

def add_pair(a, b):
    # take the input value
    # compute the result
    # return it to the caller
    result = a + b
    # hand the result back
    return result


def sub_pair(a, b):
    # take the input value
    # compute the result
    # return it to the caller
    result = a - b
    # hand the result back
    return result


def mul_pair(a, b):
    # take the input value
    # compute the result
    # return it to the caller
    result = a * b
    # hand the result back
    return result


def clamp_low(v, lo):
    # take the input value
    # compute the result
    # return it to the caller
    result = v if v > lo else lo
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def clamp_high(v, hi):
    # take the input value
    # compute the result
    # return it to the caller
    result = v if v < hi else hi
    # hand the result back
    return result


def wrap_index(i, n):
    # take the input value
    # compute the result
    # return it to the caller
    result = i % n
    # hand the result back
    return result


def scale_value(v, k):
    # take the input value
    # compute the result
    # return it to the caller
    result = v * k
    # hand the result back
    return result


def shift_value(v, k):
    # take the input value
    # compute the result
    # return it to the caller
    result = v + k
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def invert_flag(v):
    # take the input value
    # compute the result
    # return it to the caller
    result = not v
    # hand the result back
    return result


def count_chars(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = len(s)
    # hand the result back
    return result


def first_char(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s[:1]
    # hand the result back
    return result


def last_char(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s[-1:]
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def title_case(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s.capitalize()
    # hand the result back
    return result


def upper_case(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s.upper()
    # hand the result back
    return result


def lower_case(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s.lower()
    # hand the result back
    return result


def strip_space(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s.strip()
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def split_words(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = s.split()
    # hand the result back
    return result


def join_words(ws):
    # take the input value
    # compute the result
    # return it to the caller
    result = " ".join(ws)
    # hand the result back
    return result


def to_int(s):
    # take the input value
    # compute the result
    # return it to the caller
    result = int(s)
    # hand the result back
    return result


def to_str(v):
    # take the input value
    # compute the result
    # return it to the caller
    result = str(v)
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def is_even(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n % 2 == 0
    # hand the result back
    return result


def is_odd(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n % 2 == 1
    # hand the result back
    return result


def double_it(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n * 2
    # hand the result back
    return result


def halve_it(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n // 2
    # hand the result back
    return result


# ========================================
#   section: value helpers
# ========================================

def negate_it(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = -n
    # hand the result back
    return result


def absolutize(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = abs(n)
    # hand the result back
    return result


def increment(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n + 1
    # hand the result back
    return result


def decrement(n):
    # take the input value
    # compute the result
    # return it to the caller
    result = n - 1
    # hand the result back
    return result


def configure_pipeline(source, sink, workers, batch_size, timeout_ms, retries, log_level, dry_run):
    # store every knob in a plain dict
    # the call site reads them back by name
    config = {
        "source": source,
        "sink": sink,
        "workers": workers,
        "batch_size": batch_size,
        "timeout_ms": timeout_ms,
        "retries": retries,
        "log_level": log_level,
        "dry_run": dry_run,
    }
    # hand the config back
    return config


# ========================================
#  pad section 01: value helpers
# ========================================
# step 1a: normalize the value
def pad_helper_01(v):
    # normalize the value
    # return the same value
    return v
# step 1b: nothing else happens here

# ========================================
#  pad section 02: value helpers
# ========================================
# step 2a: normalize the value
def pad_helper_02(v):
    # normalize the value
    # return the same value
    return v
# step 2b: nothing else happens here

# ========================================
#  pad section 03: value helpers
# ========================================
# step 3a: normalize the value
def pad_helper_03(v):
    # normalize the value
    # return the same value
    return v
# step 3b: nothing else happens here

# ========================================
#  pad section 04: value helpers
# ========================================
# step 4a: normalize the value
def pad_helper_04(v):
    # normalize the value
    # return the same value
    return v
# step 4b: nothing else happens here

# ========================================
#  pad section 05: value helpers
# ========================================
# step 5a: normalize the value
def pad_helper_05(v):
    # normalize the value
    # return the same value
    return v
# step 5b: nothing else happens here

# ========================================
#  pad section 06: value helpers
# ========================================
# step 6a: normalize the value
def pad_helper_06(v):
    # normalize the value
    # return the same value
    return v
# step 6b: nothing else happens here

# ========================================
#  pad section 07: value helpers
# ========================================
# step 7a: normalize the value
def pad_helper_07(v):
    # normalize the value
    # return the same value
    return v
# step 7b: nothing else happens here

# ========================================
#  pad section 08: value helpers
# ========================================
# step 8a: normalize the value
def pad_helper_08(v):
    # normalize the value
    # return the same value
    return v
# step 8b: nothing else happens here

# ========================================
#  pad section 09: value helpers
# ========================================
# step 9a: normalize the value
def pad_helper_09(v):
    # normalize the value
    # return the same value
    return v
# step 9b: nothing else happens here

# ========================================
#  pad section 10: value helpers
# ========================================
# step 10a: normalize the value
def pad_helper_10(v):
    # normalize the value
    # return the same value
    return v
# step 10b: nothing else happens here

# ========================================
#  pad section 11: value helpers
# ========================================
# step 11a: normalize the value
def pad_helper_11(v):
    # normalize the value
    # return the same value
    return v
# step 11b: nothing else happens here

# ========================================
#  pad section 12: value helpers
# ========================================
# step 12a: normalize the value
def pad_helper_12(v):
    # normalize the value
    # return the same value
    return v
# step 12b: nothing else happens here

# ========================================
#  pad section 13: value helpers
# ========================================
# step 13a: normalize the value
def pad_helper_13(v):
    # normalize the value
    # return the same value
    return v
# step 13b: nothing else happens here

# ========================================
#  pad section 14: value helpers
# ========================================
# step 14a: normalize the value
def pad_helper_14(v):
    # normalize the value
    # return the same value
    return v
# step 14b: nothing else happens here

# ========================================
#  pad section 15: value helpers
# ========================================
# step 15a: normalize the value
def pad_helper_15(v):
    # normalize the value
    # return the same value
    return v
# step 15b: nothing else happens here

# ========================================
#  pad section 16: value helpers
# ========================================
# step 16a: normalize the value
def pad_helper_16(v):
    # normalize the value
    # return the same value
    return v
# step 16b: nothing else happens here

# ========================================
#  pad section 17: value helpers
# ========================================
# step 17a: normalize the value
def pad_helper_17(v):
    # normalize the value
    # return the same value
    return v
# step 17b: nothing else happens here

# ========================================
#  pad section 18: value helpers
# ========================================
# step 18a: normalize the value
def pad_helper_18(v):
    # normalize the value
    # return the same value
    return v
# step 18b: nothing else happens here

# ========================================
#  pad section 19: value helpers
# ========================================
# step 19a: normalize the value
def pad_helper_19(v):
    # normalize the value
    # return the same value
    return v
# step 19b: nothing else happens here

# ========================================
#  pad section 20: value helpers
# ========================================
# step 20a: normalize the value
def pad_helper_20(v):
    # normalize the value
    # return the same value
    return v
# step 20b: nothing else happens here

# ========================================
#  pad section 21: value helpers
# ========================================
# step 21a: normalize the value
def pad_helper_21(v):
    # normalize the value
    # return the same value
    return v
# step 21b: nothing else happens here

# ========================================
#  pad section 22: value helpers
# ========================================
# step 22a: normalize the value
def pad_helper_22(v):
    # normalize the value
    # return the same value
    return v
# step 22b: nothing else happens here
