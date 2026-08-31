# Oai -- an open-source agent that learns to write, in C.
#
#   make            build bin/oai
#   make run        build and launch it
#   make test       build and run the unit tests
#   make debug      build with sanitizers and no optimisation
#   make windows    cross-compile bin/Oai.exe with mingw-w64
#   make clean
#
# No dependencies beyond a C99 compiler and, on POSIX, pthreads + libm.
# OpenCL is loaded at runtime if present; it is never linked against.

CC      ?= cc
CSTD    ?= -std=c99
WARN     = -Wall -Wextra -Wpedantic -Wshadow -Wstrict-prototypes \
           -Wmissing-prototypes -Wno-unused-parameter
OPT     ?= -O2
INCLUDE  = -Iinclude
CFLAGS  += $(CSTD) $(WARN) $(OPT) $(INCLUDE)
LDFLAGS +=
LDLIBS   = -lm

UNAME_S := $(shell uname -s 2>/dev/null || echo Windows)
ifneq ($(UNAME_S),Windows)
  CFLAGS += -D_POSIX_C_SOURCE=200809L
  LDLIBS += -lpthread -ldl
endif

SRC_DIR = src
OBJ_DIR = build
BIN_DIR = bin

SOURCES = $(SRC_DIR)/main.c \
          $(SRC_DIR)/oai_app.c \
          $(SRC_DIR)/oai_chat.c \
          $(SRC_DIR)/oai_config.c \
          $(SRC_DIR)/oai_data.c \
          $(SRC_DIR)/oai_gpu.c \
          $(SRC_DIR)/oai_log.c \
          $(SRC_DIR)/oai_net.c \
          $(SRC_DIR)/oai_platform.c \
          $(SRC_DIR)/oai_pool.c \
          $(SRC_DIR)/oai_tensor.c \
          $(SRC_DIR)/oai_train.c \
          $(SRC_DIR)/oai_ui.c

# Everything except main.c, so the tests can link the library.
LIB_SOURCES = $(filter-out $(SRC_DIR)/main.c,$(SOURCES))
OBJECTS     = $(SOURCES:$(SRC_DIR)/%.c=$(OBJ_DIR)/%.o)
LIB_OBJECTS = $(LIB_SOURCES:$(SRC_DIR)/%.c=$(OBJ_DIR)/%.o)

TARGET   = $(BIN_DIR)/oai
TEST_SRC = $(wildcard tests/*.c)
TEST_BIN = $(TEST_SRC:tests/%.c=$(BIN_DIR)/%)

.PHONY: all run test debug windows clean install help

all: $(TARGET)

$(TARGET): $(OBJECTS) | $(BIN_DIR)
	$(CC) $(OBJECTS) -o $@ $(LDFLAGS) $(LDLIBS)
	@echo "built $@"

$(OBJ_DIR)/%.o: $(SRC_DIR)/%.c | $(OBJ_DIR)
	$(CC) $(CFLAGS) -c $< -o $@

$(OBJ_DIR) $(BIN_DIR):
	@mkdir -p $@

run: $(TARGET)
	./$(TARGET)

debug:
	$(MAKE) clean
	$(MAKE) OPT="-O0 -g -fsanitize=address,undefined" \
	        LDFLAGS="-fsanitize=address,undefined"

$(BIN_DIR)/%: tests/%.c $(LIB_OBJECTS) | $(BIN_DIR)
	$(CC) $(CFLAGS) $< $(LIB_OBJECTS) -o $@ $(LDFLAGS) $(LDLIBS)

test: $(TEST_BIN)
	@fail=0; for t in $(TEST_BIN); do \
	    echo "--- $$t"; ./$$t || fail=1; \
	done; \
	if [ $$fail -eq 0 ]; then echo "all tests passed"; else \
	    echo "TESTS FAILED"; exit 1; fi

windows:
	$(MAKE) CC=x86_64-w64-mingw32-gcc \
	        LDLIBS="-lm" \
	        TARGET=$(BIN_DIR)/Oai.exe \
	        UNAME_S=Windows \
	        $(BIN_DIR)/Oai.exe

$(BIN_DIR)/Oai.exe: $(OBJECTS) | $(BIN_DIR)
	$(CC) $(OBJECTS) -o $@ $(LDFLAGS) $(LDLIBS)

install: $(TARGET)
	install -d $(DESTDIR)/usr/local/bin
	install -m 755 $(TARGET) $(DESTDIR)/usr/local/bin/oai

clean:
	rm -rf $(OBJ_DIR) $(BIN_DIR)

help:
	@sed -n '1,12p' Makefile
