// Who is signed in (set by App from /api/auth/state); used for per-user defaults like lab names.
let ME = null;
export const setMe = (u) => { ME = u || null; };
export const me = () => ME;
// A lab name unlikely to collide with other users' labs (names are unique per server).
export const labNameFor = (base) => {
  const u = ME?.name;
  const n = u && u !== "admin" ? `${u}-${base}` : base;
  return n.toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 31).replace(/-+$/, "");
};
