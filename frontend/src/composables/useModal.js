// Small shared (singleton) modal store. Any component can call open() to
// show a modal; a single <AppModal> mounted once near the app root
// renders whatever component is currently active. Replaces the legacy
// showModal()/closeModal() DOM-injection helpers in app-common.js.
import { reactive } from "vue";

const state = reactive({ title: null, titleCode: null, component: null, props: null, size: null });

export function useModal() {
  // `size: "narrow"` is for small-content modals (e.g. Profile) so they
  // don't stretch to the same width as the PO/SKU detail modals.
  function open(title, component, props = {}, titleCode = null, size = null) {
    state.title = title;
    state.titleCode = titleCode;
    state.component = component;
    state.props = props;
    state.size = size;
  }
  function close() {
    state.title = null;
    state.titleCode = null;
    state.component = null;
    state.props = null;
    state.size = null;
  }
  return { state, open, close };
}
