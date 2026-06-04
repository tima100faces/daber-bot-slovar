--
-- PostgreSQL database dump
--

\restrict 5QetnKYTQQ6aCHrj0d00lk8VlNffBSySerfSw7NNnACGc74bQpnQCAs2ni6nbcR

-- Dumped from database version 14.23 (Ubuntu 14.23-0ubuntu0.22.04.1)
-- Dumped by pg_dump version 14.23 (Ubuntu 14.23-0ubuntu0.22.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: user_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_feedback (
    id integer NOT NULL,
    word_id integer NOT NULL,
    field_name text NOT NULL,
    selected_text text,
    comment text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    resolved boolean DEFAULT false
);


--
-- Name: user_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_feedback_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_feedback_id_seq OWNED BY public.user_feedback.id;


--
-- Name: verb_audio; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verb_audio (
    id integer NOT NULL,
    verb_id integer NOT NULL,
    form_he text,
    audio_path text,
    voice text DEFAULT 'Umbriel'::text,
    generated_at timestamp with time zone
);


--
-- Name: verb_audio_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verb_audio_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verb_audio_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verb_audio_id_seq OWNED BY public.verb_audio.id;


--
-- Name: verb_examples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verb_examples (
    id integer NOT NULL,
    verb_id integer NOT NULL,
    hebrew text,
    translation text,
    source text DEFAULT 'llm'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: verb_examples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verb_examples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verb_examples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verb_examples_id_seq OWNED BY public.verb_examples.id;


--
-- Name: verb_forms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verb_forms (
    id integer NOT NULL,
    verb_id integer NOT NULL,
    tense text NOT NULL,
    person text,
    gender text NOT NULL,
    number text NOT NULL,
    form_he text NOT NULL,
    form_he_nikud text,
    transliteration text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT verb_forms_gender_check CHECK ((gender = ANY (ARRAY['m'::text, 'f'::text, 'mf'::text]))),
    CONSTRAINT verb_forms_number_check CHECK ((number = ANY (ARRAY['singular'::text, 'plural'::text]))),
    CONSTRAINT verb_forms_person_check CHECK (((person = ANY (ARRAY['1'::text, '2'::text, '3'::text])) OR (person IS NULL))),
    CONSTRAINT verb_forms_tense_check CHECK ((tense = ANY (ARRAY['present'::text, 'past'::text, 'future'::text, 'imperative'::text, 'infinitive'::text])))
);


--
-- Name: verb_forms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verb_forms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verb_forms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verb_forms_id_seq OWNED BY public.verb_forms.id;


--
-- Name: verb_senses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verb_senses (
    id integer NOT NULL,
    verb_id integer NOT NULL,
    sense_idx integer NOT NULL,
    translation_ru text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: verb_senses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verb_senses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verb_senses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verb_senses_id_seq OWNED BY public.verb_senses.id;


--
-- Name: verb_synonyms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verb_synonyms (
    id integer NOT NULL,
    verb_id integer NOT NULL,
    hebrew text,
    translation text,
    source text DEFAULT 'llm'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: verb_synonyms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verb_synonyms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verb_synonyms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verb_synonyms_id_seq OWNED BY public.verb_synonyms.id;


--
-- Name: verbs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verbs (
    id integer NOT NULL,
    root text NOT NULL,
    binyan text NOT NULL,
    infinitive_he text NOT NULL,
    infinitive_he_nikud text,
    translation_ru text,
    pealim_slug text,
    passive_of integer,
    verified_by_human boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    translation_enriched jsonb,
    notes text,
    enriched_at timestamp with time zone,
    CONSTRAINT verbs_binyan_check CHECK ((binyan = ANY (ARRAY['paal'::text, 'piel'::text, 'hifil'::text, 'hitpael'::text, 'nifal'::text, 'pual'::text, 'hufal'::text])))
);


--
-- Name: verbs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.verbs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: verbs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.verbs_id_seq OWNED BY public.verbs.id;


--
-- Name: word_examples; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.word_examples (
    id integer NOT NULL,
    word_id integer NOT NULL,
    hebrew text,
    translation text,
    source text DEFAULT 'llm'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: word_examples_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.word_examples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: word_examples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.word_examples_id_seq OWNED BY public.word_examples.id;


--
-- Name: word_forms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.word_forms (
    id integer NOT NULL,
    word_id integer NOT NULL,
    form_he text,
    form_he_nikud text,
    translit text,
    translit_clean text,
    translation text,
    grammar_json jsonb
);


--
-- Name: word_forms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.word_forms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: word_forms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.word_forms_id_seq OWNED BY public.word_forms.id;


--
-- Name: word_phrases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.word_phrases (
    id integer NOT NULL,
    word_id integer NOT NULL,
    hebrew text,
    nikud text,
    translit text,
    translation text
);


--
-- Name: word_phrases_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.word_phrases_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: word_phrases_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.word_phrases_id_seq OWNED BY public.word_phrases.id;


--
-- Name: word_synonyms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.word_synonyms (
    id integer NOT NULL,
    word_id integer NOT NULL,
    hebrew text,
    translation text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: word_synonyms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.word_synonyms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: word_synonyms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.word_synonyms_id_seq OWNED BY public.word_synonyms.id;


--
-- Name: words; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.words (
    id integer NOT NULL,
    headword text NOT NULL,
    headword_nikud text,
    wordtype text,
    pos_slug text,
    gender text,
    grammar_json jsonb,
    frequency integer DEFAULT 0,
    frequency_rank integer DEFAULT 0,
    translation_enriched jsonb,
    notes text,
    source text DEFAULT 'iris'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    translit text
);


--
-- Name: words_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.words_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: words_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.words_id_seq OWNED BY public.words.id;


--
-- Name: user_feedback id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_feedback ALTER COLUMN id SET DEFAULT nextval('public.user_feedback_id_seq'::regclass);


--
-- Name: verb_audio id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_audio ALTER COLUMN id SET DEFAULT nextval('public.verb_audio_id_seq'::regclass);


--
-- Name: verb_examples id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_examples ALTER COLUMN id SET DEFAULT nextval('public.verb_examples_id_seq'::regclass);


--
-- Name: verb_forms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_forms ALTER COLUMN id SET DEFAULT nextval('public.verb_forms_id_seq'::regclass);


--
-- Name: verb_senses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_senses ALTER COLUMN id SET DEFAULT nextval('public.verb_senses_id_seq'::regclass);


--
-- Name: verb_synonyms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_synonyms ALTER COLUMN id SET DEFAULT nextval('public.verb_synonyms_id_seq'::regclass);


--
-- Name: verbs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verbs ALTER COLUMN id SET DEFAULT nextval('public.verbs_id_seq'::regclass);


--
-- Name: word_examples id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_examples ALTER COLUMN id SET DEFAULT nextval('public.word_examples_id_seq'::regclass);


--
-- Name: word_forms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_forms ALTER COLUMN id SET DEFAULT nextval('public.word_forms_id_seq'::regclass);


--
-- Name: word_phrases id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_phrases ALTER COLUMN id SET DEFAULT nextval('public.word_phrases_id_seq'::regclass);


--
-- Name: word_synonyms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_synonyms ALTER COLUMN id SET DEFAULT nextval('public.word_synonyms_id_seq'::regclass);


--
-- Name: words id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words ALTER COLUMN id SET DEFAULT nextval('public.words_id_seq'::regclass);


--
-- Name: verb_forms uq_verb_forms_verb_tense_pgn; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_forms
    ADD CONSTRAINT uq_verb_forms_verb_tense_pgn UNIQUE (verb_id, tense, person, gender, number);


--
-- Name: verb_senses uq_verb_senses_verb_sense_idx; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_senses
    ADD CONSTRAINT uq_verb_senses_verb_sense_idx UNIQUE (verb_id, sense_idx);


--
-- Name: user_feedback user_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_feedback
    ADD CONSTRAINT user_feedback_pkey PRIMARY KEY (id);


--
-- Name: verb_audio verb_audio_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_audio
    ADD CONSTRAINT verb_audio_pkey PRIMARY KEY (id);


--
-- Name: verb_examples verb_examples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_examples
    ADD CONSTRAINT verb_examples_pkey PRIMARY KEY (id);


--
-- Name: verb_forms verb_forms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_forms
    ADD CONSTRAINT verb_forms_pkey PRIMARY KEY (id);


--
-- Name: verb_senses verb_senses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_senses
    ADD CONSTRAINT verb_senses_pkey PRIMARY KEY (id);


--
-- Name: verb_synonyms verb_synonyms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_synonyms
    ADD CONSTRAINT verb_synonyms_pkey PRIMARY KEY (id);


--
-- Name: verbs verbs_pealim_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verbs
    ADD CONSTRAINT verbs_pealim_slug_key UNIQUE (pealim_slug);


--
-- Name: verbs verbs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verbs
    ADD CONSTRAINT verbs_pkey PRIMARY KEY (id);


--
-- Name: word_examples word_examples_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_examples
    ADD CONSTRAINT word_examples_pkey PRIMARY KEY (id);


--
-- Name: word_forms word_forms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_forms
    ADD CONSTRAINT word_forms_pkey PRIMARY KEY (id);


--
-- Name: word_phrases word_phrases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_phrases
    ADD CONSTRAINT word_phrases_pkey PRIMARY KEY (id);


--
-- Name: word_synonyms word_synonyms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_synonyms
    ADD CONSTRAINT word_synonyms_pkey PRIMARY KEY (id);


--
-- Name: words words_headword_pos_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words
    ADD CONSTRAINT words_headword_pos_slug_key UNIQUE (headword, pos_slug);


--
-- Name: words words_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.words
    ADD CONSTRAINT words_pkey PRIMARY KEY (id);


--
-- Name: idx_feedback_resolved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feedback_resolved ON public.user_feedback USING btree (resolved);


--
-- Name: idx_feedback_word_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feedback_word_id ON public.user_feedback USING btree (word_id);


--
-- Name: idx_verb_audio_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verb_audio_verb_id ON public.verb_audio USING btree (verb_id);


--
-- Name: idx_verb_examples_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verb_examples_verb_id ON public.verb_examples USING btree (verb_id);


--
-- Name: idx_verb_forms_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verb_forms_verb_id ON public.verb_forms USING btree (verb_id);


--
-- Name: idx_verb_senses_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verb_senses_verb_id ON public.verb_senses USING btree (verb_id);


--
-- Name: idx_verb_synonyms_verb_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verb_synonyms_verb_id ON public.verb_synonyms USING btree (verb_id);


--
-- Name: idx_verbs_binyan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verbs_binyan ON public.verbs USING btree (binyan);


--
-- Name: idx_verbs_enriched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verbs_enriched ON public.verbs USING btree (((enriched_at IS NOT NULL)));


--
-- Name: idx_verbs_passive_of; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verbs_passive_of ON public.verbs USING btree (passive_of);


--
-- Name: idx_verbs_root; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_verbs_root ON public.verbs USING btree (root);


--
-- Name: idx_word_examples_word_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_word_examples_word_id ON public.word_examples USING btree (word_id);


--
-- Name: idx_word_forms_word_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_word_forms_word_id ON public.word_forms USING btree (word_id);


--
-- Name: idx_word_phrases_word_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_word_phrases_word_id ON public.word_phrases USING btree (word_id);


--
-- Name: idx_word_synonyms_word_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_word_synonyms_word_id ON public.word_synonyms USING btree (word_id);


--
-- Name: idx_words_frequency_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_words_frequency_rank ON public.words USING btree (frequency_rank);


--
-- Name: idx_words_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_words_fts ON public.words USING gin (to_tsvector('simple'::regconfig, ((headword || ' '::text) || COALESCE((translation_enriched)::text, ''::text))));


--
-- Name: idx_words_headword; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_words_headword ON public.words USING btree (headword);


--
-- Name: idx_words_pos_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_words_pos_slug ON public.words USING btree (pos_slug);


--
-- Name: verb_audio verb_audio_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_audio
    ADD CONSTRAINT verb_audio_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.verbs(id) ON DELETE CASCADE;


--
-- Name: verb_examples verb_examples_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_examples
    ADD CONSTRAINT verb_examples_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.verbs(id) ON DELETE CASCADE;


--
-- Name: verb_forms verb_forms_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_forms
    ADD CONSTRAINT verb_forms_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.verbs(id) ON DELETE CASCADE;


--
-- Name: verb_senses verb_senses_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_senses
    ADD CONSTRAINT verb_senses_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.verbs(id) ON DELETE CASCADE;


--
-- Name: verb_synonyms verb_synonyms_verb_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verb_synonyms
    ADD CONSTRAINT verb_synonyms_verb_id_fkey FOREIGN KEY (verb_id) REFERENCES public.verbs(id) ON DELETE CASCADE;


--
-- Name: verbs verbs_passive_of_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verbs
    ADD CONSTRAINT verbs_passive_of_fkey FOREIGN KEY (passive_of) REFERENCES public.verbs(id) ON DELETE SET NULL;


--
-- Name: word_examples word_examples_word_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_examples
    ADD CONSTRAINT word_examples_word_id_fkey FOREIGN KEY (word_id) REFERENCES public.words(id) ON DELETE CASCADE;


--
-- Name: word_forms word_forms_word_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_forms
    ADD CONSTRAINT word_forms_word_id_fkey FOREIGN KEY (word_id) REFERENCES public.words(id) ON DELETE CASCADE;


--
-- Name: word_phrases word_phrases_word_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_phrases
    ADD CONSTRAINT word_phrases_word_id_fkey FOREIGN KEY (word_id) REFERENCES public.words(id) ON DELETE CASCADE;


--
-- Name: word_synonyms word_synonyms_word_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.word_synonyms
    ADD CONSTRAINT word_synonyms_word_id_fkey FOREIGN KEY (word_id) REFERENCES public.words(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 5QetnKYTQQ6aCHrj0d00lk8VlNffBSySerfSw7NNnACGc74bQpnQCAs2ni6nbcR

