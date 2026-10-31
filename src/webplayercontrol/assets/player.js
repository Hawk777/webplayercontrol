/*
 * Capitalizes the first character of a string.
 */
const capFirst = function (string) {
	return string.charAt(0).toUpperCase() + string.slice(1);
};

/*
 * Lowercases the first character of a string.
 */
const lowerFirst = function (string) {
	return string.charAt(0).toLowerCase() + string.slice(1);
};

/*
 * Display a status message for an error or reconnection.
 *
 * All controls are disabled.
 */
const showStatus = function (msg, error) {
	for (const button of document.getElementsByTagName("button")) {
		button.disabled = true;
	}
	const spinner = document.getElementById("spinner");
	const statusText = document.getElementById("status-text");
	statusText.textContent = msg;
	if (error) {
		spinner.classList.add("off");
		statusText.classList.add("error-message");
	} else {
		spinner.classList.remove("off");
		statusText.classList.remove("error-message");
	}
};

/*
 * Handles a server-sent event starting with “can”.
 *
 * Such an event enables or disables the indicated button.
 */
const onCan = function (evt) {
	const buttonName = lowerFirst(evt.type.slice(3));
	const button = document.getElementById(buttonName);
	button.disabled = evt.data === "0";
};

/*
 * Handles a button being clicked.
 */
const onClick = async function (evt, source) {
	const buttonName = evt.target.id;
	const result = await (async () => {
		try {
			const resp = await fetch(buttonName, { body: "", method: "POST", mode: "same-origin" });
			return [resp, null];
		} catch (err) {
			if (err instanceof TypeError) {
				return [null, err];
			}
			throw err;
		}
	})();
	const [resp, err] = result;
	if (resp === null) {
		source.close();
		showStatus(`POST to “${buttonName}” failed with ${err.message}`, true);
	} else if (!resp.ok) {
		source.close();
		showStatus(`POST to “${resp.url}” failed with HTTP ${resp.status}`, true);
	}
};

/*
 * Handles an error in the server-sent event connection.
 *
 * The problem is displayed and all controls are disabled until the event
 * stream reconnects (if it is going to do so).
 */
const onError = function (evt) {
	if (evt.target.readyState === EventSource.CONNECTING) {
		showStatus("SSE reconnecting", false);
	} else {
		showStatus("SSE connection failed", true);
	}
};

/*
 * Handles a server-sent “title” event.
 *
 * The title is displayed for the user. The loading spinner is also hidden,
 * since “title” is always the last event in the initial burst on a new
 * connection.
 */
const onTitle = function (evt) {
	const statusText = document.getElementById("status-text");
	statusText.textContent = evt.data;
	const spinner = document.getElementById("spinner");
	spinner.classList.add("off");
};

/*
 * Starts the application.
 */
const main = function () {
	const source = new EventSource("status");
	source.addEventListener("error", onError);
	for (const button of document.getElementsByTagName("button")) {
		source.addEventListener(`can${capFirst(button.id)}`, onCan);
		button.addEventListener("click", (evt) => onClick(evt, source));
	}
	source.addEventListener("title", onTitle);
};

main();
