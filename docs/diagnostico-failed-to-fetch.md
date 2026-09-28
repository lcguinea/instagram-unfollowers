# Diagnóstico de `Failed to fetch`

## Conclusión

El `TypeError: Failed to fetch` observado durante un escaneo anterior no se
reprodujo en el escaneo completo posterior. Ese escaneo terminó correctamente
y una operación manual de unfollow fue además verificada de extremo a extremo.
Por tanto, el error aislado no constituye un defecto demostrado ni justifica
añadir retry, backoff u otra corrección especulativa.

El mensaje tampoco contiene un estado HTTP ni permite atribuir una causa
concreta. Solo demuestra que aquella petición no produjo una respuesta
utilizable por `fetch`.

## Tratamiento seguro conservado

`fetchFriendshipsPage` propaga los fallos HTTP con su código. `fetchList` trata
un rechazo de red, incluido ese `TypeError`, como una fase incompleta y no lo
reintenta. El flujo de escaneo elimina resultados y selección antes de mostrar
el aviso de escaneo incompleto, por lo que una lista parcial no queda disponible
para operaciones.

Los códigos 401, 403 y 429 conservan además un motivo de bloqueo explícito y
detienen el proceso. Las respuestas sin lista de usuarios y las contradicciones
de paginación también invalidan el escaneo. Las pruebas focalizadas están en
`tests/test_fetch_friendships.py` y no realizan peticiones de red.

Este documento no contiene valores de autenticación, identificadores de cuenta
ni datos capturados de una sesión real.
