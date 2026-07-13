// =====================================================================
//  Honkai: Star Rail — Discord Dynamic Profile Widget updater
//  Port of MeYashverma/Genshin-Stats (enkaUser.js) for HSR.
//
//  Tier A (this file, NO login required):
//    Pulls PUBLIC stats from Enka.Network's HSR endpoint and a rotating
//    showcase character from your in-game profile. No HoyoLab cookie.
//
//  Tier B (optional): if a `hoyo_stats.json` file exists next to this
//    script (produced by optional-hoyolab/hsr_hoyolab.py), the Memory of
//    Chaos / Pure Fiction / Apocalyptic Shadow fields are merged into the
//    same single Discord PATCH. See README.
// =====================================================================

if (process.env.GITHUB_ACTIONS !== "true") {
    require("dotenv").config();
}
const axios = require("axios");
const fs = require("fs");
const path = require("path");

// ---- config -----------------------------------------------------------
const HSR_UID = process.env.HSR_UID;
const ENKA_HSR_URL = `https://enka.network/api/hsr/uid/${HSR_UID}`;

// StarRailRes (Mar-7th): community-standard asset index, keyed directly by
// avatarId. Auto-updates for new characters — nothing to maintain locally.
const SRRES_BASE = "https://raw.githubusercontent.com/Mar-7th/StarRailRes/master/";
const SRRES_CHARS_URL = `${SRRES_BASE}index_min/en/characters.json`;

// Enka HSR profile-picture map (only used as a fallback image).
const ENKA_PFPS_URL =
    "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/hsr/pfps.json";
const ENKA_UI_BASE = "https://enka.network"; // pfp iconPath already starts with /ui/hsr/...

const DISCORD_CLIENT_ID = process.env.DISCORD_CLIENT_ID;
const DISCORD_USER_ID = process.env.DISCORD_USER_ID;
const DISCORD_BOT_TOKEN = process.env.DISCORD_BOT_TOKEN;

// How often the displayed character rotates (in hours). Should match (or be
// a multiple of) the GitHub Actions cron schedule.
const ROTATE_HOURS = Number(process.env.ROTATE_HOURS ?? 6);

// Enka asks API consumers to send a descriptive User-Agent.
const UA = { "User-Agent": "HSR-Stats-Widget/1.0 (+github-actions)" };

// ---- region formatting ------------------------------------------------
const regionMap = {
    ASIA: "Asia",
    EUR: "Europe",
    EUROPE: "Europe",
    USA: "America",
    AMERICA: "America",
    CHT: "TW/HK/MO",
    CN: "China",
};

// =====================================================================
// SHOWCASED CHARACTER (rotating portrait + name label)
// =====================================================================
async function getShowcasedCharacter(detail) {
    try {
        const showcase = detail.avatarDetailList ?? [];
        let showcased = null;

        if (showcase.length > 0) {
            // Time-based slot: no state file needed, each scheduled run
            // lands on the next showcase slot.
            const rotationIndex =
                Math.floor(Date.now() / (ROTATE_HOURS * 3600 * 1000)) % showcase.length;
            showcased = showcase[rotationIndex];
            console.log(
                `Rotation: slot ${rotationIndex + 1}/${showcase.length} (changes every ${ROTATE_HOURS}h)`
            );
        }

        if (showcased) {
            const { data: chars } = await axios.get(SRRES_CHARS_URL, { timeout: 10000, headers: UA });
            const c = chars[String(showcased.avatarId)];

            if (c) {
                // Trailblazer entries store the name as "{NICKNAME}".
                let name = c.name;
                if (!name || name.includes("{NICKNAME}")) {
                    name = detail.nickname || "Trailblazer";
                }

                // Big splash art. Swap `portrait` -> `preview` or `icon` for
                // a tighter crop.
                const imageUrl = c.portrait ? `${SRRES_BASE}${c.portrait}` : null;

                return {
                    imageUrl,
                    name,
                    level: showcased.level ?? null,
                    eidolon: showcased.rank ?? 0, // rank == eidolon; absent means 0
                };
            }
        }

        // Fallback: player's in-game profile picture via Enka's HSR pfp map.
        const pfpId = detail.headIcon;
        if (pfpId) {
            const { data: pfps } = await axios.get(ENKA_PFPS_URL, { timeout: 10000, headers: UA });
            const iconPath = pfps[String(pfpId)]?.Icon;
            if (iconPath) {
                return { imageUrl: `${ENKA_UI_BASE}${iconPath}`, name: null, level: null, eidolon: 0 };
            }
        }

        return { imageUrl: null, name: null, level: null, eidolon: 0 };
    } catch (err) {
        console.warn("Could not resolve character image:", err.message);
        return { imageUrl: null, name: null, level: null, eidolon: 0 };
    }
}

// =====================================================================
// ACHIEVEMENT TOTAL: count of all possible achievements (StarRailRes index,
// auto-updates each game version). Used to render "797/912".
// =====================================================================
async function getAchievementTotal() {
    try {
        const { data } = await axios.get(`${SRRES_BASE}index_min/en/achievements.json`,
            { timeout: 15000, headers: UA });
        const n = Object.keys(data ?? {}).length;
        return n > 0 ? n : null;
    } catch (e) {
        console.warn("Achievement total unavailable:", e.message);
        return null; // fall back to plain earned count
    }
}

// =====================================================================
// OPTIONAL TIER B: merge HoyoLab battle stats if the helper produced them
// =====================================================================
const HOYO_STATS_MAX_AGE_H = Number(process.env.HOYO_STATS_MAX_AGE_H ?? 48);

function readHoyoStats() {
    try {
        const p = path.join(__dirname, "hoyo_stats.json");
        if (!fs.existsSync(p)) return null;
        const data = JSON.parse(fs.readFileSync(p, "utf8"));

        // Freshness guard: if the Python step has been failing (e.g. expired
        // cookie) a stale committed file shouldn't show outdated clears.
        if (data.generated_at) {
            const ageH = (Date.now() / 1000 - data.generated_at) / 3600;
            if (ageH > HOYO_STATS_MAX_AGE_H) {
                console.warn(
                    `hoyo_stats.json is ${ageH.toFixed(1)}h old (limit ${HOYO_STATS_MAX_AGE_H}h) — ` +
                    "skipping clear stats. Is the HoyoLab cookie expired?"
                );
                return null;
            }
        }

        console.log("Found hoyo_stats.json — merging MoC/PF/APC fields.");
        return data;
    } catch (e) {
        console.warn("Could not read hoyo_stats.json:", e.message);
        return null;
    }
}

// =====================================================================
async function syncHsrStats() {
    try {
        const res = await axios.get(ENKA_HSR_URL, { timeout: 10000, headers: UA });
        const detail = res.data.detailInfo;
        if (!detail) throw new Error("Player profile is private or not found.");

        const rec = detail.recordInfo ?? {};
        const region = regionMap[res.data.region] ?? res.data.region ?? "Unknown";

        // ---- resolve rotating character -------------------------------
        const character = await getShowcasedCharacter(detail);
        const { imageUrl } = character;
        const characterLabel = character.name
            ? `${character.name}${character.level ? ` • Lv. ${character.level}` : ""}` +
              `${character.eidolon ? ` • E${character.eidolon}` : ""}`
            : null;

        if (imageUrl) console.log(`Character image: ${imageUrl}`);
        if (characterLabel) console.log(`Character: ${characterLabel}`);

        const signature =
            detail.signature && detail.signature.trim() !== ""
                ? `"${detail.signature.substring(0, 60)}"`
                : '"No signature"';

        const su =
            rec.maxRogueChallengeScore != null && rec.maxRogueChallengeScore > 0
                ? `World ${rec.maxRogueChallengeScore}`
                : "—";

        // Achievements as earned/total (total from StarRailRes; may be null)
        const achTotal = await getAchievementTotal();
        const achValue = achTotal
            ? `${rec.achievementCount ?? "-"}/${achTotal}`
            : String(rec.achievementCount ?? "-");

        // ---- build the Discord dynamic payload ------------------------
        // type 1 = text, 2 = number, 3 = image
        // NOTE: Discord caps the number of dynamic fields (~30). Keep this
        // list lean — unused fields (tb/eq/col/sig/mini) were removed to make
        // room for the *_detail label lines.
        const dynamic = [
            { type: 1, name: "nickname", value: detail.nickname ?? "Trailblazer" },
            { type: 1, name: "uid", value: `UID ${HSR_UID}` },
            { type: 1, name: "world", value: `${region} • EQ ${detail.worldLevel ?? "-"}` },

            { type: 1, name: "ach_str", value: "Achievements" },
            { type: 1, name: "ach", value: achValue },

            { type: 1, name: "su_str", value: "Simulated Universe" },
            { type: 1, name: "su", value: su },
        ];
        void signature; // kept for potential future use

        // ---- Tier B: MoC / Pure Fiction / Apocalyptic Shadow ----------
        const hoyo = readHoyoStats();
        if (hoyo) {
            // Titles carry the star counts, e.g. "Memory of Chaos 36/36⭐";
            // labels (\*_detail) carry season + pts/cycles.
            if (hoyo.moc) {
                dynamic.push({ type: 1, name: "moc_str", value: `Memory of Chaos ${hoyo.moc}` });
                dynamic.push({ type: 1, name: "moc", value: hoyo.moc });
            }
            // *_detail: "Season Name • stars • pts • cycles" (label lines)
            for (const k of ["moc_detail", "pf_detail", "apc_detail"]) {
                if (hoyo[k]) dynamic.push({ type: 1, name: k, value: hoyo[k] });
            }
            if (hoyo.pf) {
                dynamic.push({ type: 1, name: "pf_str", value: `Pure Fiction ${hoyo.pf}` });
                dynamic.push({ type: 1, name: "pf", value: hoyo.pf });
            }
            if (hoyo.pf_pts) {
                dynamic.push({ type: 1, name: "pf_pts", value: hoyo.pf_pts }); // "30,000 pts"
            }
            if (hoyo.apc) {
                dynamic.push({ type: 1, name: "apc_str", value: `Apocalyptic Shadow ${hoyo.apc}` });
                dynamic.push({ type: 1, name: "apc", value: hoyo.apc });
            }
            if (hoyo.apc_pts) {
                dynamic.push({ type: 1, name: "apc_pts", value: hoyo.apc_pts });
            }
            if (hoyo.aa) {
                dynamic.push({ type: 1, name: "aa_str", value: "Anomaly Arbitration" });
                dynamic.push({ type: 1, name: "aa", value: hoyo.aa }); // e.g. "7⭐"
            }
            if (hoyo.aa_detail) {
                dynamic.push({ type: 1, name: "aa_detail", value: hoyo.aa_detail }); // "Knights 6⭐/9⭐ • King 1⭐"
            }
            if (hoyo.active_days != null) {
                dynamic.push({ type: 1, name: "days_str", value: "Active Days" });
                dynamic.push({ type: 1, name: "days_txt", value: String(hoyo.active_days) });
            }
        }

        if (imageUrl) {
            dynamic.push({ type: 3, name: "image", value: { url: imageUrl } });
        }
        if (characterLabel) {
            dynamic.push({ type: 1, name: "char", value: characterLabel });
        }

        const payload = { data: { dynamic } };

        // ---- PATCH the Discord widget ---------------------------------
        const discordApiUrl =
            `https://discord.com/api/v9/applications/${DISCORD_CLIENT_ID}` +
            `/users/${DISCORD_USER_ID}/identities/0/profile`;

        const response = await axios.patch(discordApiUrl, payload, {
            headers: {
                Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
                "Content-Type": "application/json",
            },
        });

        console.log(`Synced HSR widget for ${detail.nickname}. Status: ${response.status}`);
    } catch (error) {
        if (error.response) {
            console.error("Discord/Enka API Error:", error.response.status,
                JSON.stringify(error.response.data, null, 2));
            process.exit(1);
        } else {
            console.error("Request Error:", error.message);
            process.exit(1);
        }
    }
}

syncHsrStats();
