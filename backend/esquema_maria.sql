-- ONIX-ESQUEMA v1 (generado por scripts/generar_esquema_maria.py; no editar a mano)
-- ;;ONIX;;
CREATE TABLE `audios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `titulo` text NOT NULL,
  `artista` text NOT NULL,
  `album` text DEFAULT NULL,
  `duracion` double DEFAULT 0,
  `archivo_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `categoria` text DEFAULT NULL,
  `subgenero` text DEFAULT NULL,
  `genero_vocal` text DEFAULT NULL,
  `bpm` int(11) DEFAULT NULL,
  `intro` double DEFAULT NULL,
  `outro` double DEFAULT NULL,
  `hook` double DEFAULT NULL,
  `fecha_lanzamiento` text DEFAULT NULL,
  `epoca` text DEFAULT NULL,
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `updated_at` text NOT NULL DEFAULT current_timestamp(),
  `cat3` text DEFAULT NULL,
  `cue_inicio` double DEFAULT NULL,
  `cue_intro` double DEFAULT NULL,
  `cue_inicio_coro` double DEFAULT NULL,
  `cue_final_coro` double DEFAULT NULL,
  `cue_mezcla` double DEFAULT NULL,
  `fade_in` double DEFAULT NULL,
  `fade_out` double DEFAULT NULL,
  `ultima_reproduccion` text DEFAULT NULL,
  `veces_reproducido` int(11) NOT NULL DEFAULT 0,
  `tipo_audio` text NOT NULL DEFAULT 'Musica',
  `loudness_lufs` double DEFAULT NULL,
  `pico_db` double DEFAULT NULL,
  `loudness_estado` text DEFAULT NULL,
  `ganancia_db` double DEFAULT NULL,
  `origen` text DEFAULT NULL,
  `energia` int(11) DEFAULT NULL,
  `marcas_auto` int(11) DEFAULT NULL,
  `aprobada` int(11) NOT NULL DEFAULT 1,
  `restr_desde` int(11) DEFAULT NULL,
  `restr_hasta` int(11) DEFAULT NULL,
  `restr_dias` text DEFAULT NULL,
  `max_por_dia` int(11) DEFAULT NULL,
  `prioridad` int(11) DEFAULT NULL,
  `cue_outro` double DEFAULT NULL,
  `repeticion_horas` int(11) DEFAULT NULL,
  `busq` text NOT NULL DEFAULT '',
  `categoria_ident` text DEFAULT NULL,
  `meta_modo` text NOT NULL DEFAULT '',
  `meta_titulo` text NOT NULL DEFAULT '',
  `meta_artista` text NOT NULL DEFAULT '',
  `meta_extra1` text NOT NULL DEFAULT '',
  `meta_extra2` text NOT NULL DEFAULT '',
  `ambito` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `archivo_path` (`archivo_path`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `cartuchera` (
  `banco` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `slot` int(11) NOT NULL,
  `audio_id` int(11) NOT NULL,
  `etiqueta` text NOT NULL DEFAULT '',
  `updated_at` text NOT NULL DEFAULT current_timestamp(),
  `color` text NOT NULL DEFAULT '',
  `mark_in` double DEFAULT NULL,
  `mark_out` double DEFAULT NULL,
  `volumen` int(11) NOT NULL DEFAULT 100,
  PRIMARY KEY (`banco`,`slot`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `clientes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `slug` text DEFAULT NULL,
  `nombre` text DEFAULT NULL,
  `ciudad` text DEFAULT NULL,
  `genero` text DEFAULT NULL,
  `zona_horaria` text DEFAULT NULL,
  `modo` text DEFAULT NULL,
  `entrega_cifrada` text DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `comerciales_agencias` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(500) NOT NULL,
  `contacto` text NOT NULL DEFAULT '',
  `telefono` text NOT NULL DEFAULT '',
  `email` text NOT NULL DEFAULT '',
  `notas` text NOT NULL DEFAULT '',
  `creado_en` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `comerciales_clientes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(500) NOT NULL,
  `contacto` text NOT NULL DEFAULT '',
  `telefono` text NOT NULL DEFAULT '',
  `email` text NOT NULL DEFAULT '',
  `notas` text NOT NULL DEFAULT '',
  `creado_en` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `comerciales_conflictos_rubros` (
  `a` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `b` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  PRIMARY KEY (`a`,`b`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `comerciales_rubros` (
  `nombre` varchar(500) NOT NULL,
  PRIMARY KEY (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `config` (
  `clave` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `valor` text NOT NULL,
  PRIMARY KEY (`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `config_categorias_def` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre_interno` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `etiqueta_visible` text NOT NULL,
  `orden` int(11) NOT NULL DEFAULT 0,
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `updated_at` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `nombre_interno` (`nombre_interno`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `config_categorias_valores` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cat_id` int(11) NOT NULL,
  `valor` varchar(150) NOT NULL,
  `orden` int(11) NOT NULL DEFAULT 0,
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `color` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `cat_id` (`cat_id`,`valor`),
  CONSTRAINT `1` FOREIGN KEY (`cat_id`) REFERENCES `config_categorias_def` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `fuentes_noticias` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cliente_id` int(11) DEFAULT NULL,
  `nombre` text DEFAULT NULL,
  `url` text DEFAULT NULL,
  `activo` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `historial_emision` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `audio_id` int(11) DEFAULT NULL,
  `titulo` text NOT NULL DEFAULT '',
  `artista` text NOT NULL DEFAULT '',
  `tipo` text NOT NULL DEFAULT 'musica',
  `origen` text NOT NULL DEFAULT 'manual',
  `pauta_id` int(11) DEFAULT NULL,
  `slot` text DEFAULT NULL,
  `iniciado_en` text NOT NULL DEFAULT current_timestamp(),
  `tanda` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `ia_claves` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` text NOT NULL,
  `proveedor` text NOT NULL,
  `modelo` text NOT NULL DEFAULT '',
  `base_url` text NOT NULL DEFAULT '',
  `clave` text NOT NULL,
  `orden` int(11) NOT NULL DEFAULT 0,
  `activo` int(11) NOT NULL DEFAULT 1,
  `ultimo_ok` text DEFAULT NULL,
  `ultimo_error` text DEFAULT NULL,
  `ultimo_error_en` text DEFAULT NULL,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `voces` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `ident_categorias` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `tipo_audio` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `nombre` varchar(150) NOT NULL,
  `comentario` text NOT NULL DEFAULT '',
  `orden` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tipo_audio` (`tipo_audio`,`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_config` (
  `clave` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `valor` text NOT NULL DEFAULT 'null',
  PRIMARY KEY (`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_datos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `ambito` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `clave` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `etiqueta` text NOT NULL DEFAULT '',
  `dato` text NOT NULL DEFAULT '',
  `fuente` text NOT NULL DEFAULT '',
  `estado` text NOT NULL DEFAULT 'ok',
  `revisado_en` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ambito` (`ambito`,`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_fuentes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` text NOT NULL,
  `url` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `cierre` text NOT NULL DEFAULT '',
  `activo` int(11) NOT NULL DEFAULT 1,
  `revisada_en` text DEFAULT NULL,
  `error` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `url` (`url`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_intervenciones` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `tipo` text NOT NULL,
  `estado` text NOT NULL DEFAULT 'borrador',
  `modo` text NOT NULL DEFAULT 'auto',
  `persona` text NOT NULL DEFAULT '',
  `voz` text NOT NULL DEFAULT '',
  `texto` text DEFAULT NULL,
  `audio_id` int(11) DEFAULT NULL,
  `duracion_seg` double DEFAULT NULL,
  `anterior_id` int(11) DEFAULT NULL,
  `siguiente_id` int(11) DEFAULT NULL,
  `origen_id` int(11) DEFAULT NULL,
  `proveedor_voz` text DEFAULT NULL,
  `bytes_tts` int(11) NOT NULL DEFAULT 0,
  `error` text DEFAULT NULL,
  `motivo` text DEFAULT NULL,
  `creado_en` text NOT NULL DEFAULT current_timestamp(),
  `decidido_en` text DEFAULT NULL,
  `al_aire_en` text DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_mensajes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `de` text NOT NULL,
  `para` text NOT NULL DEFAULT '',
  `lugar` text NOT NULL DEFAULT '',
  `texto` text NOT NULL DEFAULT '',
  `cancion` text NOT NULL DEFAULT '',
  `estado` text NOT NULL DEFAULT 'pendiente',
  `motivo` text DEFAULT NULL,
  `origen` text NOT NULL DEFAULT 'panel',
  `prioridad` int(11) NOT NULL DEFAULT 0,
  `audio_id` int(11) DEFAULT NULL,
  `creado_en` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_noticias` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `fuente_id` int(11) DEFAULT NULL,
  `guid` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `titulo` text NOT NULL,
  `resumen` text NOT NULL DEFAULT '',
  `enlace` text DEFAULT NULL,
  `publicada_en` text DEFAULT NULL,
  `estado` text NOT NULL DEFAULT 'nueva',
  `prioridad` int(11) NOT NULL DEFAULT 0,
  `creado_en` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `guid` (`guid`),
  KEY `fuente_id` (`fuente_id`),
  CONSTRAINT `1` FOREIGN KEY (`fuente_id`) REFERENCES `locutor_fuentes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_patrocinios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` text NOT NULL DEFAULT '',
  `texto` text NOT NULL,
  `desde` text NOT NULL,
  `hasta` text NOT NULL,
  `frecuencia` int(11) NOT NULL DEFAULT 1,
  `activo` int(11) NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `locutor_turnos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `desde` text NOT NULL,
  `hasta` text NOT NULL,
  `nombre` text NOT NULL,
  `voz` text NOT NULL,
  `personalidad` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `paletas` (
  `clave` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `nombre` text NOT NULL,
  `slots` int(11) NOT NULL DEFAULT 9,
  `orden` int(11) NOT NULL DEFAULT 0,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `patrocinios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cliente_id` int(11) DEFAULT NULL,
  `texto` text DEFAULT NULL,
  `desde` text DEFAULT NULL,
  `hasta` text DEFAULT NULL,
  `frecuencia` int(11) DEFAULT NULL,
  `activo` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `pautas` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cliente` text NOT NULL,
  `audio_id` int(11) NOT NULL,
  `audio_nombre` text NOT NULL DEFAULT '',
  `matriz` text NOT NULL DEFAULT '{}',
  `fecha_inicio` text DEFAULT '',
  `fecha_fin` text DEFAULT '',
  `notas` text DEFAULT '',
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `updated_at` text NOT NULL DEFAULT current_timestamp(),
  `cue_intro` double DEFAULT NULL,
  `cue_outro` double DEFAULT NULL,
  `menciones_diarias` int(11) DEFAULT NULL,
  `rubro` text DEFAULT NULL,
  `prioridad` int(11) DEFAULT NULL,
  `contrato_id` text DEFAULT NULL,
  `seccion` int(11) NOT NULL DEFAULT 3,
  `agencia` text DEFAULT '',
  `posicion` int(11) DEFAULT 0,
  `orden` int(11) DEFAULT NULL,
  `hora_inicio_permitida` int(11) DEFAULT NULL,
  `hora_fin_permitida` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `pautas_deuda` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `pauta_id` int(11) NOT NULL,
  `slot_original` varchar(40) NOT NULL,
  `fecha` varchar(20) NOT NULL,
  `motivo` varchar(60) DEFAULT 'no_emitida',
  `recuperada` int(11) DEFAULT 0,
  `recuperada_en` varchar(30) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_pauta_fecha` (`pauta_id`,`fecha`,`recuperada`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `pauta_audios` (
  `pauta_id` int(11) NOT NULL,
  `audio_id` int(11) NOT NULL,
  `orden` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`pauta_id`,`audio_id`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`pauta_id`) REFERENCES `pautas` (`id`) ON DELETE CASCADE,
  CONSTRAINT `2` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `personas_dj` (
  `cliente_id` int(11) DEFAULT NULL,
  `nombre` text DEFAULT NULL,
  `personalidad` text DEFAULT NULL,
  `muletillas` text DEFAULT NULL,
  `prohibidos` text DEFAULT NULL,
  `proveedor_voz` text DEFAULT NULL,
  `voz_id` text DEFAULT NULL,
  `pronunciacion` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `playlists` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `descripcion` text DEFAULT NULL,
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `reloj_id` int(11) DEFAULT NULL,
  `horas` double DEFAULT NULL,
  `programada` text NOT NULL DEFAULT '',
  `inmediato` int(11) NOT NULL DEFAULT 0,
  `autor` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `playlist_audios` (
  `playlist_id` int(11) NOT NULL,
  `audio_id` int(11) NOT NULL,
  `posicion` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`playlist_id`,`audio_id`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`playlist_id`) REFERENCES `playlists` (`id`) ON DELETE CASCADE,
  CONSTRAINT `2` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `playlist_items` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `playlist_id` int(11) NOT NULL,
  `posicion` int(11) NOT NULL,
  `audio_id` int(11) NOT NULL,
  `nota` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `playlist_id` (`playlist_id`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`playlist_id`) REFERENCES `playlists` (`id`) ON DELETE CASCADE,
  CONSTRAINT `2` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `reglas` (
  `cliente_id` int(11) DEFAULT NULL,
  `intervenciones_por_hora` int(11) DEFAULT NULL,
  `tipos_permitidos` text DEFAULT NULL,
  `franjas` text DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `relojes` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `color` text NOT NULL DEFAULT '#0a9396',
  `comentario` text NOT NULL DEFAULT '',
  `compensacion` text NOT NULL DEFAULT '{}',
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `updated_at` text NOT NULL DEFAULT current_timestamp(),
  `respaldo` text NOT NULL DEFAULT '{}',
  PRIMARY KEY (`id`),
  UNIQUE KEY `nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `reloj_dias` (
  `fecha` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `hora` int(11) NOT NULL,
  `reloj_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`fecha`,`hora`),
  KEY `reloj_id` (`reloj_id`),
  CONSTRAINT `1` FOREIGN KEY (`reloj_id`) REFERENCES `relojes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `reloj_eventos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` text NOT NULL,
  `reloj_id` int(11) NOT NULL,
  `hora` text NOT NULL,
  `dias` text NOT NULL DEFAULT '0,1,2,3,4,5,6',
  `fecha_inicio` text NOT NULL DEFAULT '',
  `fecha_fin` text NOT NULL DEFAULT '',
  `inmediato` int(11) NOT NULL DEFAULT 0,
  `activo` int(11) NOT NULL DEFAULT 1,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  `hasta` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `reloj_id` (`reloj_id`),
  CONSTRAINT `1` FOREIGN KEY (`reloj_id`) REFERENCES `relojes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `reloj_filas` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `reloj_id` int(11) NOT NULL,
  `orden` int(11) NOT NULL DEFAULT 0,
  `tipo_audio` text NOT NULL DEFAULT 'Musica',
  `filtros` text NOT NULL DEFAULT '{}',
  `audio_id` int(11) DEFAULT NULL,
  `nota` text NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  KEY `reloj_id` (`reloj_id`),
  KEY `audio_id` (`audio_id`),
  CONSTRAINT `1` FOREIGN KEY (`reloj_id`) REFERENCES `relojes` (`id`) ON DELETE CASCADE,
  CONSTRAINT `2` FOREIGN KEY (`audio_id`) REFERENCES `audios` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `reloj_grilla` (
  `dia` int(11) NOT NULL,
  `hora` int(11) NOT NULL,
  `reloj_id` int(11) NOT NULL,
  PRIMARY KEY (`dia`,`hora`),
  KEY `reloj_id` (`reloj_id`),
  CONSTRAINT `1` FOREIGN KEY (`reloj_id`) REFERENCES `relojes` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `sesiones` (
  `token_hash` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `usuario_id` int(11) NOT NULL,
  `expira` text NOT NULL,
  `ip` text DEFAULT NULL,
  `creada` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`token_hash`),
  KEY `usuario_id` (`usuario_id`),
  CONSTRAINT `1` FOREIGN KEY (`usuario_id`) REFERENCES `usuarios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `tanda_ajustes` (
  `fecha` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `hora` int(11) NOT NULL,
  `minuto` int(11) NOT NULL,
  `ids` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT '[]',
  PRIMARY KEY (`fecha`,`hora`,`minuto`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `tanda_quitadas` (
  `fecha` varchar(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `hora` int(11) NOT NULL,
  `pauta_id` int(11) NOT NULL,
  PRIMARY KEY (`fecha`,`hora`,`pauta_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `tanda_secciones` (
  `numero` int(11) NOT NULL,
  `nombre` text NOT NULL,
  `opcional` int(11) NOT NULL DEFAULT 0,
  `comercial` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`numero`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `turnos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cliente_id` int(11) DEFAULT NULL,
  `desde` text DEFAULT NULL,
  `hasta` text DEFAULT NULL,
  `nombre` text DEFAULT NULL,
  `voz_id` text DEFAULT NULL,
  `personalidad` text DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TABLE `usuarios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `usuario` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
  `nombre` text NOT NULL DEFAULT '',
  `rol` text NOT NULL,
  `clave_hash` text NOT NULL,
  `activo` int(11) NOT NULL DEFAULT 1,
  `ultimo_acceso` text DEFAULT NULL,
  `created_at` text NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `usuario` (`usuario`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
-- ;;ONIX;;
CREATE TRIGGER trg_audios_busq_ins BEFORE INSERT ON audios FOR EACH ROW SET NEW.busq = LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(CONCAT(COALESCE(NEW.titulo, ''), ' ', COALESCE(NEW.artista, ''), ' ', COALESCE(NEW.subgenero, ''), ' ', COALESCE(NEW.categoria, '')), 'á', 'a'), 'é', 'e'), 'í', 'i'), 'ó', 'o'), 'ú', 'u'), 'ü', 'u'), 'ñ', 'n'), 'Á', 'A'), 'É', 'E'), 'Í', 'I'), 'Ó', 'O'), 'Ú', 'U'), 'Ü', 'U'), 'Ñ', 'N'))
-- ;;ONIX;;
CREATE TRIGGER trg_audios_busq_upd BEFORE UPDATE ON audios FOR EACH ROW SET NEW.busq = LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(CONCAT(COALESCE(NEW.titulo, ''), ' ', COALESCE(NEW.artista, ''), ' ', COALESCE(NEW.subgenero, ''), ' ', COALESCE(NEW.categoria, '')), 'á', 'a'), 'é', 'e'), 'í', 'i'), 'ó', 'o'), 'ú', 'u'), 'ü', 'u'), 'ñ', 'n'), 'Á', 'A'), 'É', 'E'), 'Í', 'I'), 'Ó', 'O'), 'Ú', 'U'), 'Ü', 'U'), 'Ñ', 'N'))
-- ;;ONIX;;
