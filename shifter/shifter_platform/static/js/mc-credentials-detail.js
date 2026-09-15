// Mission Control credential detail page: delete a stored credential.
//
// Extracted from the inline <script> block in
// templates/mission_control/credentials/detail.html so the template stays
// within Sonar's Web:LongJavaScriptCheck limit. The CSRF token and the
// credentials-list redirect URL are passed in via the
// #mc-credentials-detail-config json_script payload.

document.addEventListener('DOMContentLoaded', function () {
    const configEl = document.getElementById('mc-credentials-detail-config');
    const config = configEl ? JSON.parse(configEl.textContent) : {};
    const csrfToken = config.csrfToken;
    const deleteBtn = document.getElementById('delete-btn');

    if (deleteBtn) {
        deleteBtn.addEventListener('click', function () {
            const credId = this.dataset.credentialId;
            const credName = this.dataset.credentialName;

            if (confirm('Are you sure you want to delete "' + credName + '"? This cannot be undone.')) {
                fetch('/api/v1/mission-control/credentials/' + credId + '/delete/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken
                    }
                })
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    if (data.error) {
                        alert(data.error);
                    } else {
                        globalThis.location.href = config.listUrl;
                    }
                })
                .catch(function () {
                    alert('An error occurred. Please try again.');
                });
            }
        });
    }
});
