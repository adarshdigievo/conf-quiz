export function splitJoinUrl(value) {
  const boundaries = [value.indexOf("?"), value.indexOf("#")].filter((index) => index >= 0);
  const boundary = boundaries.length ? Math.min(...boundaries) : value.length;
  return {
    base: value.slice(0, boundary),
    parameters: value.slice(boundary),
  };
}
