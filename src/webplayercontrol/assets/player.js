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
 * Indicates that a status is an error.
 */
const STATUS_ERROR = Symbol("error");

/*
 * Indicates that a status is a transient value that should be shown with a
 * spinner.
 */
const STATUS_TRANSIENT = Symbol("transient");

/*
 * Indicates that a status is due to the page being hidden and is therefore
 * transient but should not have a spinner.
 */
const STATUS_HIDDEN = Symbol("hidden");

/*
 * Indicates that the status text is the media title and the system is working
 * properly.
 */
const STATUS_WORKING = Symbol("working");

/*
 * Displays a status message for an error or reconnection.
 *
 * All controls are disabled.
 */
const showStatus = function (msg, mode) {
	if (mode !== STATUS_WORKING) {
		for (const button of document.getElementsByTagName("button")) {
			button.disabled = true;
		}
	}
	const spinner = document.getElementById("spinner");
	const statusText = document.getElementById("status-text");
	statusText.textContent = msg;
	switch (mode) {
		case STATUS_ERROR:
			spinner.classList.add("off");
			statusText.classList.add("error-message");
			break;
		case STATUS_TRANSIENT:
			spinner.classList.remove("off");
			statusText.classList.remove("error-message");
			break;
		case STATUS_HIDDEN:
		case STATUS_WORKING:
			spinner.classList.add("off");
			statusText.classList.remove("error-message");
			break;
	}
};

/*
 * A connection to the SSE stream.
 *
 * The SSEConnection emits an “open” event when the connection is successful
 * and an “error” event if the connection fails to connect or fails during
 * usage. No event is emitted if it is closed by a call to the “close” method.
 */
class SSEConnection extends EventTarget {
	/*
	 * The event source.
	 */
	#source;

	/*
	 * Starts an SSEConnection.
	 */
	constructor() {
		super();
		this.#source = new EventSource("status");
		this.#source.addEventListener("error", this.#onError.bind(this));
		for (const button of document.getElementsByTagName("button")) {
			this.#source.addEventListener(`can${capFirst(button.id)}`, SSEConnection.#onCan);
		}
		this.#source.addEventListener("title", this.#onTitle.bind(this));
	}

	/*
	 * Closes an SSEConnection.
	 */
	close() {
		this.#source.close();
	}

	/*
	 * Handles a server-sent event starting with “can”.
	 *
	 * Such an event enables or disables the indicated button.
	 */
	static #onCan(evt) {
		const buttonName = lowerFirst(evt.type.slice(3));
		const button = document.getElementById(buttonName);
		button.disabled = evt.data === "0";
	}

	/*
	 * Handles a server-sent “title” event.
	 *
	 * The title is displayed for the user. The loading spinner is also hidden,
	 * since “title” is always the last event in the initial burst on a new
	 * connection.
	 */
	#onTitle(evt) {
		showStatus(evt.data, STATUS_WORKING);
		this.dispatchEvent(new Event("open"));
	};

	/*
	 * Handles an error in the server-sent event connection.
	 */
	#onError() {
		if (this.#source.readyState === EventSource.CONNECTING) {
			showStatus("SSE reconnecting", STATUS_TRANSIENT);
		} else {
			this.dispatchEvent(new Event("error"));
		}
	};
};

/*
 * The application.
 */
class App {
	/*
	 * The current connection, if any.
	 */
	#connection;

	/*
	 * How many times we have failed to open a connection.
	 */
	#failCount;

	/*
	 * Whether the application has failed.
	 */
	#failed;

	/*
	 * Starts the app.
	 */
	constructor() {
		this.#connection = null;
		this.#failCount = 0;
		this.#failed = false;
		for (const button of document.getElementsByTagName("button")) {
			button.addEventListener("click", this.#onClick.bind(this));
		}
		addEventListener("visibilitychange", this.#onVisibilityChanged.bind(this));
		showStatus("SSE connecting", STATUS_TRANSIENT);
		this.#open();
	}

	/*
	 * Handles a button being clicked.
	 */
	async #onClick(evt) {
		if (this.#failed) {
			return;
		}
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
		if (resp !== null && resp.ok) {
			return;
		}
		this.#failed = true;
		if (this.#connection !== null) {
			this.#connection.close();
			this.#connection = null;
		}
		if (resp === null) {
			showStatus(`POST to “${buttonName}” failed with ${err.message}`, STATUS_ERROR);
		} else {
			showStatus(`POST to “${resp.url}” failed with HTTP ${resp.status}`, STATUS_ERROR);
		}
	};

	/*
	 * Handles the page visibility changing.
	 */
	#onVisibilityChanged() {
		if (this.#failed) {
			return;
		}
		if (document.hidden && this.#connection !== null) {
			this.#connection.close();
			this.#connection = null;
			showStatus("SSE closed due to page visibility", STATUS_HIDDEN);
		} else if (!document.hidden && this.#connection === null) {
			showStatus("SSE reopening after page visibility", STATUS_TRANSIENT);
			this.#open();
		}
	};

	/*
	 * Opens an SSE connection.
	 */
	#open() {
		this.#connection = new SSEConnection();
		this.#connection.addEventListener("open", this.#onOpen.bind(this));
		this.#connection.addEventListener("error", this.#onError.bind(this));
	}

	/*
	 * Handles the SSE connection opening successfully.
	 */
	#onOpen() {
		this.#failCount = 0;
	}

	/*
	 * Handles the SSE connection failing.
	 */
	#onError() {
		this.#connection = null;
		++this.#failCount;
		if (this.#failCount < 3) {
			showStatus("SSE retrying failed connection", STATUS_TRANSIENT);
			this.#open();
		} else {
			this.#failed = true;
			showStatus("SSE connection failed", STATUS_ERROR);
		}
	}
};

new App();
