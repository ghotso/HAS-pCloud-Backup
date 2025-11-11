(() => {
  const PATCHED_ATTR = "data-pcloud-backup-icon";
  const TARGET_SUFFIX = "/_/pcloud_backup/icon.png";

  const createCloudIcon = () => {
    const icon = document.createElement("ha-svg-icon");
    icon.setAttribute("icon", "mdi:cloud");
    icon.setAttribute("style", "flex-shrink:0;width:24px;height:24px;");
    return icon;
  };

  const applyPatch = (img) => {
    if (img.hasAttribute(PATCHED_ATTR)) {
      return;
    }

    const cloudIcon = createCloudIcon();
    img.replaceWith(cloudIcon);
    cloudIcon.setAttribute(PATCHED_ATTR, "1");
  };

  const scanAndPatch = () => {
    document
      .querySelectorAll(`img[src$="${TARGET_SUFFIX}"]`)
      .forEach(applyPatch);
  };

  const observer = new MutationObserver(scanAndPatch);

  const start = () => {
    scanAndPatch();
    observer.observe(document.body, { childList: true, subtree: true });
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

