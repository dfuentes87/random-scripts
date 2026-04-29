function sortEssaysByDate(data) {
    return data.sort((a, b) => new Date(b.date) - new Date(a.date));
}

const postsPerPage = 20;
let currentPage = 1;
let allEssays = [];

function populateEssays(data) {
    const essaysContainer = document.getElementById('essays-container');
    const list = data.map(essay => `
        <li>
            <a href="../${essay.html_link}" target="_blank">${essay.title}</a>
            <div class="subtitle">${essay.subtitle}</div>
            <div class="metadata">${essay.date}</div>
        </li>
    `).join('');
    essaysContainer.innerHTML = `<ul>${list}</ul>`;
}

function getTotalPages() {
    return Math.max(1, Math.ceil(allEssays.length / postsPerPage));
}

function displayCurrentPage() {
    const startIndex = (currentPage - 1) * postsPerPage;
    populateEssays(allEssays.slice(startIndex, startIndex + postsPerPage));
    updatePaginationControls();
}

function updatePaginationControls() {
    const totalPages = getTotalPages();

    const pageInfo = document.getElementById('page-info');
    if (pageInfo) {
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    }

    const prevButton = document.getElementById('prev-page');
    if (prevButton) {
        prevButton.disabled = currentPage === 1;
    }

    const nextButton = document.getElementById('next-page');
    if (nextButton) {
        nextButton.disabled = currentPage === totalPages;
    }
}

function changePage(delta) {
    const nextPage = currentPage + delta;
    if (nextPage < 1 || nextPage > getTotalPages()) {
        return;
    }

    currentPage = nextPage;
    displayCurrentPage();
}

document.addEventListener('DOMContentLoaded', () => {
    const embeddedDataElement = document.getElementById('essaysData');
    if (!embeddedDataElement) {
        return;
    }

    allEssays = sortEssaysByDate([...JSON.parse(embeddedDataElement.textContent)]);
    displayCurrentPage();

    const prevButton = document.getElementById('prev-page');
    if (prevButton) {
        prevButton.addEventListener('click', () => changePage(-1));
    }

    const nextButton = document.getElementById('next-page');
    if (nextButton) {
        nextButton.addEventListener('click', () => changePage(1));
    }
});
