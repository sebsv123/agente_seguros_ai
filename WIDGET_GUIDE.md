# Guía de Integración del Widget de Rosa Valentín 🩺

Este widget es un botón flotante premium diseñado para aumentar la conversión web hacia tu Instagram DM.

## 1. Cómo integrarlo en tu web
Para que el widget aparezca en tu sitio, simplemente copia el código del archivo `rosa_widget.html` y pégalo justo antes de la etiqueta de cierre `</body>` de tu `index.html` (o en el footer de tu CMS como WordPress/Wix).

### Pasos rápidos:
1. Abre `c:\Users\Sebitas\agente-seguros\rosa_widget.html`.
2. Copia todo el contenido.
3. Pégalo en tu web.

## 2. Configuración Obligatoria
Debes cambiar el enlace de Instagram por el tuyo real. Busca esta línea en el código:

```html
<a href="https://ig.me/m/YOUR_INSTAGRAM_HANDLE" target="_blank" style="text-decoration: none;">
```

Reemplaza `YOUR_INSTAGRAM_HANDLE` por tu nombre de usuario de Instagram (ej: `rosa_seguros`).

## 3. Personalización (Opcional)

### Cambiar Colores
El widget usa un gradiente moderno. Si quieres cambiarlo, busca la clase `.rosa-widget-bubble` en el CSS y modifica:
```css
background: linear-gradient(135deg, #FF6B6B 0%, #FF8E53 100%); /* Tu color aquí */
```

### Cambiar Posición
Por defecto está en la esquina inferior derecha. Puedes moverlo ajustando:
```css
.rosa-widget-container {
    bottom: 20px; /* Distancia del suelo */
    right: 20px;  /* Distancia de la derecha */
}
```

## 4. Cómo funciona para el cliente
1. El cliente ve el botón con un aviso sutil: *"¡Hola! ¿Te ayudo a resolver tus dudas sobre seguros?"*.
2. Al hacer clic, se abre una nueva pestaña directamente en su Instagram (App o Web) con el chat abierto contigo.
3. El agente de backend detectará el mensaje entrante y comenzará el flujo automático ("Rosa" entrará en acción).
