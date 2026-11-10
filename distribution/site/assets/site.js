(() => {
  async function copyWith(text, button, okMessage, failMessage) {
    const status = document.getElementById(button.getAttribute('data-copy-status') || 'verification-copy-status');
    const fallback = document.getElementById(button.getAttribute('data-copy-fallback') || 'verification-copy-fallback');
    if (!status || !fallback) return;
    fallback.value = text;

    if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      try {
        await navigator.clipboard.writeText(text);
        fallback.hidden = true;
        status.textContent = okMessage;
        return;
      } catch {}
    }

    fallback.hidden = false;
    fallback.focus();
    fallback.select();
    status.textContent = failMessage;
  }

  const section = document.getElementById('verification');
  const verify = section?.querySelector('[data-copy-verification]');
  if (verify) {
    verify.hidden = false;
    verify.addEventListener('click', () => {
      const packageValue = document.getElementById('verification-package').textContent.trim();
      const signerValue = document.getElementById('verification-signer').textContent.trim();
      const copyText = packageValue + '\n' + signerValue;
      copyWith(copyText, verify, 'Copied package and APK signing SHA256.', 'Clipboard unavailable. Both lines are selected below; choose Copy from your device menu.');
    });
  }

  document.querySelectorAll('[data-copy-source]').forEach((button) => {
    const source = document.getElementById(button.getAttribute('data-copy-source'));
    if (!source) return;
    button.hidden = false;
    button.addEventListener('click', () => {
      copyWith(source.textContent.trim(), button, button.getAttribute('data-copy-label') || 'Copied.', 'Clipboard unavailable. The text is selected below; choose Copy from your device menu.');
    });
  });
})();
