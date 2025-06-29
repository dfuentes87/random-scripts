// Always sort descending by date (newest first)
function sortEssaysByDate(data) {
    return data.sort((a, b) => new Date(b.date) - new Date(a.date));
}

const postsPerPage = 20;
let currentPage = 1;
let allEssays = [];

// This function populates the essays container with a list of posts
function populateEssays(data) {
    const essaysContainer = document.getElementById('essays-container');
    const list = data.map(essay => `
        <li>
            <a href="../${essay.html_link}">${essay.title}</a>
            <div class="subtitle">${essay.subtitle}</div>
            <div class="metadata">${essay.date}</div>
        </li>
    `).join('');
    essaysContainer.innerHTML = `<ul>${list}</ul>`;
}

// This function calculates the posts for the current page and displays them
function displayCurrentPage() {
    const startIndex = (currentPage - 1) * postsPerPage;
    const endIndex = startIndex + postsPerPage;
    const pageData = allEssays.slice(startIndex, endIndex);
    populateEssays(pageData);
    updatePaginationControls();
}

// This function updates the pagination controls (page number, disabling buttons, etc.)
function updatePaginationControls() {
    const totalPages = Math.ceil(allEssays.length / postsPerPage);

    // Update the page info display, if it exists
    const pageInfo = document.getElementById('page-info');
    if (pageInfo) {
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    }

    // Enable/disable Previous button
    const prevButton = document.getElementById('prev-page');
    if (prevButton) {
        prevButton.disabled = currentPage === 1;
    }

    // Enable/disable Next button
    const nextButton = document.getElementById('next-page');
    if (nextButton) {
        nextButton.disabled = currentPage === totalPages;
    }
}

// Set up the page on DOM load
document.addEventListener('DOMContentLoaded', () => {
    const embeddedDataElement = document.getElementById('essaysData');
    let essaysData = JSON.parse(embeddedDataElement.textContent);
    
    // Sort the essays and store them in a global variable
    allEssays = sortEssaysByDate([...essaysData]);
    
    // Display the first page
    displayCurrentPage();

    // Set up event listener for the Previous button
    const prevButton = document.getElementById('prev-page');
    if (prevButton) {
        prevButton.addEventListener('click', () => {
            if (currentPage > 1) {
                currentPage--;
                displayCurrentPage();
            }
        });
    }

    // Set up event listener for the Next button
    const nextButton = document.getElementById('next-page');
    if (nextButton) {
        nextButton.addEventListener('click', () => {
            const totalPages = Math.ceil(allEssays.length / postsPerPage);
            if (currentPage < totalPages) {
                currentPage++;
                displayCurrentPage();
            }
        });
    }
});
