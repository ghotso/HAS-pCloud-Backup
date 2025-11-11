const PATCHED_ATTR = "data-pcloud-backup-icon";
const TARGET_SUFFIX = "/_/pcloud_backup/icon.png";
const REPLACEMENT_SRC = "/pcloud_backup_static/cloud.svg";

const applyPatch = (img) => {
  if (img.hasAttribute(PATCHED_ATTR)) {
    return;
  }
  img.setAttribute(PATCHED_ATTR, "1");
  img.src = REPLACEMENT_SRC;
};

const scanAndPatch = () => {
  document
    .querySelectorAll(`img[src$="${TARGET_SUFFIX}"]`)
    .forEach(applyPatch);
};

const observer = new MutationObserver(() => scanAndPatch());

if (window.customElements) {
  window.addEventListener("DOMContentLoaded", () => {
    scanAndPatch();
    observer.observe(document.body, { childList: true, subtree: true });
  });
} else {
  scanAndPatch();
}


