(() => {
	const method =
		"ccpcerp.ccpc_erp.mrs_permissions.get_workspace_approval_shortcut_visibility";
	let allowed_labels = new Set();
	let managed_labels = new Set();
	let visibility_loaded = false;

	function get_shortcut_label(widget) {
		const title = widget.querySelector(".widget-title span");
		return (title && (title.getAttribute("title") || title.textContent || "").trim()) || "";
	}

	function apply_visibility(root) {
		if (!visibility_loaded || !root || root.nodeType !== 1) return;

		const widgets = [];
		if (root.classList && root.classList.contains("shortcut-widget-box")) {
			widgets.push(root);
		}
		if (root.querySelectorAll) {
			widgets.push(...root.querySelectorAll(".shortcut-widget-box"));
		}

		widgets.forEach((widget) => {
			const label = get_shortcut_label(widget);
			if (!managed_labels.has(label)) return;

			const block = widget.closest(".ce-block") || widget;
			block.style.display = allowed_labels.has(label) ? "" : "none";
		});
	}

	function apply_all() {
		if (!visibility_loaded) return;
		document.querySelectorAll(".shortcut-widget-box").forEach((widget) => {
			apply_visibility(widget);
		});
	}

	async function load_visibility() {
		try {
			const response = await frappe.call({ method });
			const message = (response && response.message) || {};
			allowed_labels = new Set(message.allowed || []);
			managed_labels = new Set(message.managed || []);
			visibility_loaded = true;
			apply_all();
		} catch (error) {
			console.warn("CCPC approval shortcut visibility could not be loaded.", error);
		}
	}

	function start() {
		if (!window.frappe || !frappe.call || !document.body) {
			setTimeout(start, 100);
			return;
		}

		load_visibility();

		const observer = new MutationObserver((mutations) => {
			mutations.forEach((mutation) => {
				mutation.addedNodes.forEach((node) => {
					if (node.nodeType === 1) apply_visibility(node);
				});
			});
		});
		observer.observe(document.body, { childList: true, subtree: true });

		if (frappe.router && frappe.router.on) {
			frappe.router.on("change", () => {
				setTimeout(apply_all, 100);
			});
		}
	}

	start();
})();
