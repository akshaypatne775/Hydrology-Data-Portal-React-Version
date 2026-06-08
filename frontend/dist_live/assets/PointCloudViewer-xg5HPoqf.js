import{i as e,n as t,t as n}from"./jsx-runtime-CUBmso4R.js";var r=e(t(),1),i=n();function a({url:e}){return(0,i.jsx)(`div`,{style:{position:`relative`,width:`100%`,height:`100%`,background:`#06171b`},children:(0,i.jsx)(`iframe`,{src:e,style:{width:`100%`,height:`100%`,border:`none`,display:`block`},title:`Droid 3D Point Cloud System`})})}function o({url:e,name:t=`Point Cloud`}){let n=(0,r.useRef)(null);return(0,r.useEffect)(()=>{let e=e=>{if(!e.getElementById(`droid-clean-3d-viewer-style`)){let t=e.createElement(`style`);t.id=`droid-clean-3d-viewer-style`,t.textContent=`
          #potree_render_area,
          .potree_render_area,
          .potree_container {
            inset: 0 !important;
            left: 0 !important;
            width: 100% !important;
            height: 100% !important;
            margin: 0 !important;
          }
          #potree_sidebar_container,
          #potree_branding,
          #potree_map_toggle,
          #potree_map,
          .potree-branding,
          .potree_branding,
          .potree-logo,
          .potree_logo,
          [class*="potree-brand"],
          [class*="potree-logo"],
          [id*="potree-brand"],
          [id*="potree-logo"] {
            display: none !important;
            visibility: hidden !important;
            opacity: 0 !important;
            pointer-events: none !important;
          }
        `,e.head?.appendChild(t)}e.querySelectorAll(`body *`).forEach(e=>{let t=e.children.length===0?e.textContent?.trim().toLowerCase():``,n=e instanceof HTMLImageElement?e.src.toLowerCase():``;(t===`potree`||n.includes(`potree`))&&e.style.setProperty(`display`,`none`,`important`)})},t=()=>{let t=n.current;t&&(e(document),t.querySelectorAll(`iframe`).forEach(t=>{try{t.contentDocument&&e(t.contentDocument)}catch{}}))};t();let r=window.setInterval(t,500);return()=>window.clearInterval(r)},[e]),(0,i.jsx)(`section`,{ref:n,className:`point-cloud-viewer`,"aria-label":`${t} 3D data viewer`,children:(0,i.jsx)(a,{url:e},e)})}export{o as default};