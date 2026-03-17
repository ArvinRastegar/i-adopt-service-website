import SvgCss  from '../../css/svg.css?raw';
import toTurtle from '../model/toTurtle';
import { state } from './editor/state';


/**
 * get a plain string representation of the current visualization in SVG
 * removes the editor-related entries
 *
 * @returns {string}
 */
function getPlainSVG() {

  // the active SVG within the DOM
  const domEl = document.querySelector( '#svg' );

  // clone, so we can work with it
  const copyEl = domEl.cloneNode( true );

  // remove any editor-related content
  for( const el of copyEl.querySelectorAll( '.menu') ) {
    el.remove();
  }

  // return as string
  return copyEl.innerHTML;

}



/**
 * export current visualization to SVG
 *
 * @returns {Blob}
 */
export function getSVGBlob() {

  // get SVG content and prepare for download
  let content = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + getPlainSVG();
  content = content
    .replace( '<svg', '<svg xmlns="http://www.w3.org/2000/svg"' )
    .replace( '<defs>', `<defs><style>${SvgCss}</style>`);

  // done
  return new Blob([content], {type: 'image/svg+xml;charset=utf-8' });

}



/**
 * export current visualization to PNG
 *
 * @returns {Promise<Blob>}
 */
export async function getPNGBlob() {

  // https://stackoverflow.com/a/74026755/1169798

  // grab data URI
  const dataUri = URL.createObjectURL( getSVGBlob() );
  const svg = document.querySelector( '#svg svg' );
  const img = document.createElement( 'img' );

  // convert to PNG
  return new Promise( (resolve, reject) => {

    img.onerror = (e) => reject( new Error( 'Failed to generate PNG', { cause: e } ) );
    img.onload = () => {

      try {
        const canvas = document.createElement( 'canvas');
        canvas.width = 2 * svg.clientWidth;
        canvas.height = 2 * svg.clientHeight;
        canvas.getContext('2d')
          .drawImage( img, 0, 0, 2 * svg.clientWidth, 2 * svg.clientHeight );

        canvas.toBlob( resolve, 'image/png' );
      } catch(e) {
        reject(e);
      }

    };

    img.src = dataUri;

  });

}


/**
 * return the Turtle currently shown to the user
 *
 * @returns {string}
 */
export function getCurrentTurtle() {

  // The backend-generated TTL shown in the textarea is the publish/download source of truth.
  const inputEl = document.querySelector( '#input' );
  const textareaTTL = inputEl?.value?.trim();

  if( textareaTTL ) {
    return textareaTTL;
  }

  // Fall back to the local model only when no backend TTL is currently visible.
  return toTurtle( state.variable );

}


/**
 * export current variable to Turtle
 */
export function getTurtleBlob() {

  // Export the exact TTL the user is currently reviewing so download and publish stay aligned.
  const ttl = getCurrentTurtle();

  // done
  return new Blob([ttl], {type: 'text/turtle;charset=utf-8' });

}
