// Host capabilities (/api/system), fetched once and shared: features this host can't run are flagged in the UI.
import { api } from "./api.js";
let p = null;
export const systemInfo = () => (p ||= api.system().catch(() => { p = null; return null; }));
export const missingFeatures = (sys, needs) => (needs || []).filter((f) => sys?.features?.[f] && !sys.features[f].ok);
