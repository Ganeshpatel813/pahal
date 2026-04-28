/**
 * auth.js — session check on every page load.
 * Uses no-cache fetch so browser cache cannot bypass logout.
 */
document.addEventListener("DOMContentLoaded", async () => {
    const elIn    = document.getElementById("nav-logged-in");
    const elOut   = document.getElementById("nav-logged-out");
    const elAdmin = document.getElementById("nav-admin");
    const elName  = document.getElementById("nav-name");

    try {
        const res = await fetch("/api/auth/me", {
            cache: "no-store",
            headers: { "Cache-Control": "no-cache" }
        });
        if (!res.ok) throw new Error("not logged in");
        const user = await res.json();

        elOut?.classList.add("hidden");
        if (user.role === "admin") {
            elAdmin?.classList.remove("hidden"); elAdmin?.classList.add("flex");
            elIn?.classList.add("hidden");
        } else {
            elIn?.classList.remove("hidden"); elIn?.classList.add("flex");
            elAdmin?.classList.add("hidden");
        }
        if (elName) elName.textContent = "👤 " + user.name;
    } catch {
        elIn?.classList.add("hidden");
        elAdmin?.classList.add("hidden");
        elOut?.classList.remove("hidden");
    }
});
