/**
 * CTF time-zone select preselection tests (#582).
 */

function buildMarkup(selected) {
    const mark = (value) => (value === selected ? ' selected' : '');
    return `
        <form>
            <select name="time_zone" id="id_time_zone">
                <option value=""${mark('')}>Event time zone (UTC)</option>
                <option value="Asia/Tokyo"${mark('Asia/Tokyo')}>Asia/Tokyo</option>
                <option value="Europe/Paris"${mark('Europe/Paris')}>Europe/Paris</option>
            </select>
        </form>
    `;
}

function loadModule(browserZone, selected = '') {
    jest.resetModules();
    jest.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => ({
        resolvedOptions: () => ({ timeZone: browserZone }),
    }));
    document.body.innerHTML = buildMarkup(selected);
    require('./ctf-time-zone-select.js');
    document.dispatchEvent(new Event('DOMContentLoaded'));
    return document.getElementById('id_time_zone');
}

afterEach(() => {
    jest.restoreAllMocks();
});

describe('ctf-time-zone-select', () => {
    test('selects the browser zone when it is an option', () => {
        expect(loadModule('Europe/Paris').value).toBe('Europe/Paris');
    });

    test('keeps the event default when the browser zone is not an option', () => {
        expect(loadModule('Mars/Olympus').value).toBe('');
    });

    test('keeps a value the server already selected', () => {
        expect(loadModule('Europe/Paris', 'Asia/Tokyo').value).toBe('Asia/Tokyo');
    });
});
