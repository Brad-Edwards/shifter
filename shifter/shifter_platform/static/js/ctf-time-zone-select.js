/**
 * Preselect the browser time zone in CTF time-zone selects (#582).
 *
 * Sets every ``select[name="time_zone"]`` that still has no value to the
 * browser's IANA zone when that zone is one of its options. Pure enhancement:
 * with JavaScript disabled the select keeps its "event time zone" default.
 * CSP-safe (no inline handlers).
 */

function browserTimeZone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    } catch {
        return '';
    }
}

function preselect(select, zone) {
    if (select.value !== '') {
        return;
    }
    if (Array.from(select.options).some((candidate) => candidate.value === zone)) {
        select.value = zone;
    }
}

function initTimeZoneSelect() {
    const zone = browserTimeZone();
    if (!zone) {
        return;
    }
    document.querySelectorAll('select[name="time_zone"]').forEach((select) => preselect(select, zone));
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTimeZoneSelect);
} else {
    initTimeZoneSelect();
}
