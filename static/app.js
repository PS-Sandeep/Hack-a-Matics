document.querySelectorAll('.drop-zone').forEach(zone => {
    const input = document.getElementById(zone.dataset.input);
    const nameBox = document.getElementById(`${zone.dataset.input.replace('_file', '')}-file-name`);

    zone.addEventListener('click', () => input.click());

    input.addEventListener('change', () => {
        nameBox.textContent = input.files.length ? input.files[0].name : 'No file selected';
    });

    ['dragenter', 'dragover'].forEach(eventName => {
        zone.addEventListener(eventName, event => {
            event.preventDefault();
            zone.classList.add('dragging');
        });
    });

    ['dragleave', 'drop'].forEach(eventName => {
        zone.addEventListener(eventName, event => {
            event.preventDefault();
            zone.classList.remove('dragging');
        });
    });

    zone.addEventListener('drop', event => {
        const files = event.dataTransfer.files;
        if (files.length) {
            input.files = files;
            nameBox.textContent = files[0].name;
        }
    });
});

const warehouseCount = document.getElementById('number_of_warehouses');
warehouseCount.addEventListener('input', () => {
    warehouseCount.value = warehouseCount.value.replace(/\D/g, '');
});

document.getElementById('optimizer-form').addEventListener('submit', () => {
    const button = document.getElementById('submit-button');
    button.disabled = true;
    button.querySelector('span:first-child').textContent = 'Optimizing…';
});
