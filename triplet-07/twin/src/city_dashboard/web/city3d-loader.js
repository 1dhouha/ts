import('/3d/assets/city3d-viewer.js').catch(() => {
  const root = document.getElementById('root');
  if (root) root.textContent = 'Dashboard application could not be loaded. Reload the page and reconnect.';
});
