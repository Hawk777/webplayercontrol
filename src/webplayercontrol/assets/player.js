const capFirst = function(string) {
	return string.charAt(0).toUpperCase() + string.slice(1);
};

const lowerFirst = function(string) {
	return string.charAt(0).toLowerCase() + string.slice(1);
};

const onCan = function(evt) {
	const buttonName = lowerFirst(evt.type.slice(3));
	const button = document.getElementById(buttonName);
	button.disabled = evt.data === "0";
};

const onError = function(evt) {
	for(const button of document.getElementsByTagName("button")) {
		button.disabled = true;
	}
	const spinner = document.getElementById("spinner");
	const statusText = document.getElementById("status-text");
	if(evt.target.readyState === EventSource.CONNECTING) {
		spinner.classList.remove("off");
		statusText.textContent = "Reconnecting";
		statusText.classList.remove("error-message");
	} else {
		spinner.classList.add("off");
		statusText.textContent = "Connection failed";
		statusText.classList.add("error-message");
	}
};

const onTitle = function(evt) {
	const statusText = document.getElementById("status-text");
	statusText.textContent = evt.data;
	const spinner = document.getElementById("spinner");
	spinner.classList.add("off");
};

const main = function() {
	const source = new EventSource("status");
	source.addEventListener("error", onError);
	for(const button of document.getElementsByTagName("button")) {
		source.addEventListener(`can${capFirst(button.id)}`, onCan);
	}
	source.addEventListener("title", onTitle);
};

main();
