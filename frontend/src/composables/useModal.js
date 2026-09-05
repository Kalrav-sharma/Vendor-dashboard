// Small shared (singleton) modal store. Any component can call open() to
// show a modal; a single <AppModal> mounted once near the app root
// renders whatever component is currently active. Replaces the legacy
// showModal()/closeModal() DOM-injection helpers in app-common.js.
import { reactive } from "vue";

const state = reactive({ title: null, titleCode: null, component: null, props: null });

export function useModal() {
  function open(title, component, props = {}, titleCode = null) {
    state.title = title;
    state.titleCode = titleCode;
    state.component = component;
    state.props = props;
  }
  function close() {
    state.title = null;
    state.titleCode = null;
    state.component = null;
    state.props = null;
  }
  return { state, open, close };
}
