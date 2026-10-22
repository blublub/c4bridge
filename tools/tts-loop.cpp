// Derived from llama.cpp tools/tts/tts.cpp - https://github.com/ggml-org/llama.cpp
// Copyright (c) 2023-2026 The ggml authors. MIT License (see THIRD_PARTY_NOTICES.md).
// c4bridge: llama-tts als Dauerprozess – Modell einmal laden, dann Satz für Satz über stdin.
// Anfrage (eine Zeile, UTF-8):  <stimmdatei>\t<ausgabe.wav>\t<text>
// Antwort (stdout):             @@OK <rechenzeit_s> <ton_s>   bzw.   @@ERR <meldung>
// Beim Start nach dem Laden:    @@READY
// Gleiche Erzeugung wie tools/tts/tts.cpp; zwischen den Sätzen wird der KV-Speicher geleert.
#include "arg.h"
#include "common.h"
#include "sampling.h"
#include "log.h"
#include "llama.h"
#include "mtmd.h"
#include "mtmd-helper.h"

#include <cstdio>
#include <iostream>
#include <map>
#include <string>

static void reply(const std::string & s) {
    fprintf(stdout, "%s\n", s.c_str());
    fflush(stdout);
}

int main(int argc, char ** argv) {
    common_params params;
    common_init();
    if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_TTS, nullptr)) {
        return 1;
    }
    common_log_set_verbosity_thold(-1);           // Protokoll nur bei Fehlern (stdout bleibt für Antworten frei)
    mtmd_helper_log_set(common_log_default_callback, nullptr);
    if (params.mmproj.path.empty()) {
        reply("@@ERR kein mmproj (--mmproj)");
        return 1;
    }
    params.embedding = true;
    llama_backend_init();
    llama_numa_init(params.numa);

    auto llama_init = common_init_from_params(params);
    llama_model    * model = llama_init->model();
    llama_context  * lctx  = llama_init->context();
    common_sampler * smpl  = llama_init->sampler(0);
    if (!model || !lctx) {
        reply("@@ERR Modell nicht geladen");
        return 1;
    }
    mtmd_context_params mtmd_params = mtmd_context_params_default();
    mtmd_params.use_gpu = params.mmproj_use_gpu;
    mtmd_params.device  = params.mmproj_device;
    mtmd::context_ptr mctx(mtmd_init_from_file(params.mmproj.path.c_str(), model, mtmd_params));
    if (!mctx || mtmd_gen_audio_get_info(mctx.get()).type == MTMD_GEN_AUDIO_TYPE_NONE) {
        reply("@@ERR mmproj ohne Audio-Erzeugung");
        return 1;
    }

    std::map<std::string, mtmd::bitmap_ptr> speakers;   // Stimmvorlagen nur einmal einlesen
    reply("@@READY");

    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        const size_t a = line.find('\t'), b = a == std::string::npos ? a : line.find('\t', a + 1);
        if (b == std::string::npos) { reply("@@ERR Format: stimme<TAB>ausgabe<TAB>text"); continue; }
        const std::string spk = line.substr(0, a), out = line.substr(a + 1, b - a - 1), text = line.substr(b + 1);

        mtmd_bitmap * spk_bm = nullptr;
        if (!spk.empty()) {
            auto it = speakers.find(spk);
            if (it == speakers.end()) {
                auto w = mtmd_helper_bitmap_init_from_file(mctx.get(), spk.c_str(), false, mtmd_helper_init_opt_default());
                if (!w.bitmap) { reply("@@ERR Stimmdatei nicht lesbar: " + spk); continue; }
                it = speakers.emplace(spk, mtmd::bitmap_ptr(w.bitmap)).first;
            }
            spk_bm = it->second.get();
        }

        llama_memory_clear(llama_get_memory(lctx), true);
        common_sampler_reset(smpl);
        const int64_t t0 = ggml_time_us();

        mtmd_helper::gen_audio gen(lctx, mctx.get());
        mtmd_helper_gen_audio_inp inp{};
        inp.seq_id      = 0;
        inp.prompt      = text.c_str();
        inp.prompt_len  = text.size();
        inp.speaker_ref = spk_bm;
        inp.lang        = params.tts_lang.c_str();
        inp.top_k       = params.sampling.top_k;
        inp.top_p       = params.sampling.top_p;
        inp.seed        = params.sampling.seed;
        inp.out_type    = MTMD_HELPER_GEN_AUDIO_OUTTYPE_WAV;
        if (gen.set_input(&inp) != 0) { reply("@@ERR set_input"); continue; }

        bool fail = false;
        for (;;) {
            const int32_t ret = gen.step_prompt(params.n_batch);
            if (ret < 0) { fail = true; break; }
            if (ret == 0) break;
        }
        if (fail) { reply("@@ERR Prompt"); continue; }

        auto sample = [&]() -> llama_token {
            llama_token t = common_sampler_sample(smpl, lctx, -1);
            common_sampler_accept(smpl, t, true);
            return t;
        };
        const int max_new = params.n_predict > 0 ? params.n_predict : 512;
        int n_frames = 0;
        llama_token sampled = sample();
        const float * h_state = llama_get_embeddings_ith(lctx, -1);
        bool stop = false;
        while (!stop && n_frames < max_new) {
            const float * h_next = nullptr;
            if (gen.step_gen(sampled, h_state, &h_next, &stop) != 0) { fail = true; break; }
            if (!h_next) break;
            n_frames++;
            h_state = h_next;
            sampled = sample();
        }
        if (fail) { reply("@@ERR Erzeugung"); continue; }

        int32_t sr = 0; const char * data = nullptr; size_t len = 0; int64_t n_samples = 0;
        if (gen.get_output(&sr, &data, &len, &n_samples) != 0) { reply("@@ERR Ausgabe"); continue; }
        FILE * f = fopen(out.c_str(), "wb");
        if (!f) { reply("@@ERR Datei: " + out); continue; }
        fwrite(data, 1, len, f);
        fclose(f);
        char buf[96];
        snprintf(buf, sizeof(buf), "@@OK %.2f %.2f", (ggml_time_us() - t0) / 1e6, sr > 0 ? (double) n_samples / sr : 0.0);
        reply(buf);
    }
    llama_backend_free();
    return 0;
}
