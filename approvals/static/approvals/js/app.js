// Barre latérale réductible (préférence retenue par utilisateur) et modale de
// confirmation du système (remplace window.confirm() du navigateur) pour les
// actions irréversibles (suppression de brouillon, déconnexion).

document.addEventListener("DOMContentLoaded", function () {
    initSidebarToggle();
    initConfirmModal();
});

function initSidebarToggle() {
    var toggle = document.getElementById("sidebar-toggle");
    var shell = document.querySelector(".app-shell");
    if (!toggle || !shell) return;

    if (localStorage.getItem("sidebarCollapsed") === "1") {
        shell.classList.add("sidebar-collapsed");
    }

    toggle.addEventListener("click", function () {
        var collapsed = shell.classList.toggle("sidebar-collapsed");
        localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
    });
}

function initConfirmModal() {
    var modal = document.getElementById("confirm-modal");
    if (!modal) return;

    var messageEl = document.getElementById("confirm-modal-message");
    var cancelBtn = document.getElementById("confirm-modal-cancel");
    var confirmBtn = document.getElementById("confirm-modal-confirm");
    var pendingForm = null;

    function openModal(form) {
        pendingForm = form;
        messageEl.textContent = form.getAttribute("data-confirm");
        modal.classList.remove("hidden");
    }

    function closeModal() {
        pendingForm = null;
        modal.classList.add("hidden");
    }

    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (form.dataset.confirmed === "1") return;
            event.preventDefault();
            openModal(form);
        });
    });

    cancelBtn.addEventListener("click", closeModal);
    modal.addEventListener("click", function (event) {
        if (event.target === modal) closeModal();
    });
    confirmBtn.addEventListener("click", function () {
        var form = pendingForm;
        closeModal();
        if (form) {
            form.dataset.confirmed = "1";
            form.requestSubmit ? form.requestSubmit() : form.submit();
        }
    });
}
