function load(): number {
  try { return read(); }
  catch (e) { return 0; }
}
