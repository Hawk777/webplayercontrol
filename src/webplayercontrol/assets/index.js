const onLoad = async function () {
	const playerList = document.getElementById("player-list");
	try {
		// Fetch the list of players from the server.
		const listingResp = await fetch("players/", { headers: { Accept: "application/json" } });
		if (!listingResp.ok) {
			throw new Error(`Fetching player list failed: HTTP error ${listingResp.status}`);
		}
		const listing = await listingResp.json();
		for (const [name, display] of listing) {
			const item = document.createElement("li");
			const link = document.createElement("a");
			link.href = `players/${name}/`;
			link.textContent = display;
			item.append(link);
			playerList.append(item);
		}
	} catch (exp) {
		const para = document.createElement("p");
		para.textContent = exp.toString();
		para.classList.add("error-message");
		playerList.parentElement.replaceChild(para, playerList);
	}
};

window.addEventListener("load", onLoad);
