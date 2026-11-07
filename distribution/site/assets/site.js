(() => {
  const section = document.getElementById('verification');
  const button = section?.querySelector('[data-copy-verification]');
  const status = document.getElementById('verification-copy-status');
  const fallback = document.getElementById('verification-copy-fallback');
  if (!button || !status || !fallback) return;

  button.hidden = false;
  button.addEventListener('click', async () => {
    const packageValue = document.getElementById('verification-package').textContent.trim();
    const signerValue = document.getElementById('verification-signer').textContent.trim();
    const copyText = 'Package: ' + packageValue + '\nAPK signing SHA256: ' + signerValue;
    fallback.value = copyText;

    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(copyText);
        fallback.hidden = true;
        status.textContent = 'Copied package and APK signing SHA256.';
        return;
      } catch {}
    }

    fallback.hidden = false;
    fallback.focus();
    fallback.select();
    status.textContent = 'Clipboard unavailable. Both lines are selected below; choose Copy from your device menu.';
  });
})();
