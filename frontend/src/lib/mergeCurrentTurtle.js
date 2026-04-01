import N3 from 'n3';
import toTurtle from '../model/toTurtle';
import { parseRDF } from './parse';
import { NS } from './extract';

const { DataFactory, Writer, Store } = N3;
const { namedNode } = DataFactory;

const RDF_TYPE = namedNode( NS.rdf + 'type' );
const VARIABLE_CLASS = NS.iop + 'Variable';

const MANAGED_TYPE_IRIS = new Set([
  NS.iop + 'Variable',
  NS.iop + 'Entity',
  NS.iop + 'Property',
  NS.iop + 'StatisticalModifier',
  NS.iop + 'Constraint',
  NS.iop + 'System',
  NS.iop + 'SymmetricSystem',
  NS.iop + 'AsymmetricSystem',
]);

const MANAGED_PREDICATE_IRIS = new Set([
  NS.rdfs + 'label',
  NS.rdfs + 'comment',
  NS.iop + 'hasObjectOfInterest',
  NS.iop + 'hasProperty',
  NS.iop + 'hasMatrix',
  NS.iop + 'hasContextObject',
  NS.iop + 'hasStatisticalModifier',
  NS.iop + 'hasConstraint',
  NS.iop + 'hasPart',
  NS.iop + 'hasSource',
  NS.iop + 'hasTarget',
  NS.iop + 'hasNumerator',
  NS.iop + 'hasDenominator',
  NS.iop + 'constrains',
]);

function getFirstVariableTerm( store ) {
  const matches = store.getQuads( null, RDF_TYPE, namedNode( VARIABLE_CLASS ), null );
  return matches[0]?.subject ?? null;
}

function collectManagedSubjects( store, variableTerm ) {
  if( !variableTerm ) {
    return new Set();
  }

  const queue = [ variableTerm ];
  const visited = new Set();

  while( queue.length > 0 ) {
    const subject = queue.shift();
    const subjectKey = subject.value;

    if( visited.has( subjectKey ) ) {
      continue;
    }
    visited.add( subjectKey );

    // Follow only the structural predicates that the visualizer itself controls.
    for( const quad of store.getQuads( subject, null, null, null ) ) {
      if( !MANAGED_PREDICATE_IRIS.has( quad.predicate.value ) ) {
        continue;
      }
      if( quad.object.termType !== 'NamedNode' && quad.object.termType !== 'BlankNode' ) {
        continue;
      }
      queue.push( quad.object );
    }
  }

  return visited;
}

function isManagedQuad( quad, managedSubjects ) {
  if( !managedSubjects.has( quad.subject.value ) ) {
    return false;
  }

  if( quad.predicate.equals( RDF_TYPE ) ) {
    return MANAGED_TYPE_IRIS.has( quad.object.value );
  }

  return MANAGED_PREDICATE_IRIS.has( quad.predicate.value );
}

function serializeStore( store, prefixes ) {
  return new Promise( (resolve, reject) => {
    const writer = new Writer({ prefixes });
    writer.addQuads( store.getQuads( null, null, null, null ) );
    writer.end( (error, result) => {
      if( error ) {
        reject( error );
        return;
      }
      resolve( result );
    } );
  } );
}

export default async function mergeCurrentTurtle( currentTurtle, variable ) {
  const currentContent = currentTurtle?.trim();
  if( !currentContent ) {
    return toTurtle( variable );
  }

  try {
    const [ currentParsed, updatedParsed ] = await Promise.all([
      parseRDF( currentContent ),
      parseRDF( toTurtle( variable ) ),
    ]);

    const currentStore = new Store( currentParsed.store.getQuads( null, null, null, null ) );
    const updatedStore = updatedParsed.store;

    const currentManagedSubjects = collectManagedSubjects( currentStore, getFirstVariableTerm( currentStore ) );
    const updatedManagedSubjects = collectManagedSubjects( updatedStore, getFirstVariableTerm( updatedStore ) );

    // Remove only the triples the visualizer is authoritative for, so backend-added metadata stays untouched.
    for( const quad of currentStore.getQuads( null, null, null, null ) ) {
      if( isManagedQuad( quad, currentManagedSubjects ) ) {
        currentStore.removeQuad( quad );
      }
    }

    // Reinsert the freshly edited visualizer triples on top of the preserved graph.
    for( const quad of updatedStore.getQuads( null, null, null, null ) ) {
      if( isManagedQuad( quad, updatedManagedSubjects ) ) {
        currentStore.addQuad( quad );
      }
    }

    return await serializeStore( currentStore, {
      ... currentParsed.prefixes,
      ... updatedParsed.prefixes,
    } );
  } catch (e) {
    console.error( e );
    // Fall back to the visualizer serialization instead of leaving the editor unusable.
    return toTurtle( variable );
  }
}
